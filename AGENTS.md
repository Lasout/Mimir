# AGENTS.md — contexte projet tradeHelper

Ce document est la **mémoire de projet** : lis-le avant toute modification.
La feuille de route détaillée est dans [`ROADMAP.md`](ROADMAP.md).

## Qu'est-ce que c'est

Outil d'aide au trading **swing / day trading**, en **Python 3 + Tkinter** (GUI) +
pandas/numpy/matplotlib. Données **ouvertes et gratuites** (pas de clé API) :
- actions/ETF : Yahoo Finance via `yfinance`
- crypto : API publique Binance (klines)
- forex : taux quotidiens BCE via `frankfurter.dev`

Cache CSV local dans `~/.cache/tradehelper/` (TTL 6 h en daily, 5 min en intraday,
repli automatique hors-ligne). Yahoo rate-limite parfois (429) : le cache sert de repli.

## Architecture

```
run.py                        # point d'entrée GUI : python3 run.py
tradehelper/
├── config.py                 # configuration (défauts < fichier JSON $TRADEHELPER_CONFIG < env)
├── timeutil.py               # utcnow_naive() (pandas 3.x)
├── data/
│   ├── store.py              # Store SQLite (~/.local/share/tradehelper/market.db) :
│   │                         #   table bars + meta, upsert incrémental, thread-safe
│   ├── providers.py          # chaîne de fournisseurs : Yahoo, Binance (paginé 1000/appel),
│   │                         #   Frankfurter (forex daily), Alpha Vantage (si clé env)
│   ├── sources.py            # load_data(...) : sert depuis le store, ne télécharge que les
│   │                         #   plages manquantes (tête « max » sondée 1x/24h + queue fraîche
│   │                         #   selon TTL) ; périodes : 1mo..5y + max
│   ├── events.py             # calendrier des résultats (earnings) via yfinance, caché 24 h
│   └── universe.py           # listes d'actifs connus par source (actions/crypto/forex)
├── indicators.py             # SMA/EMA, RSI Wilder, MACD, Bollinger, ATR, ADX, Donchian, OBV, VWAP...
├── strategies/               # registre STRATEGIES : ma_cross, rsi_reversion,
│                             #   donchian_breakout, macd_momentum, adaptive_regime (ADX),
│                             #   ml_signal (sklearn, features causales, fenêtre glissante)
├── fees.py                   # FeeModel (fixe + % + min/max par exécution) ; préréglages dont
│                             #   Trade Republic FR par défaut (1 €/ordre actions, 1 % min 1 € crypto)
├── backtest/
│   ├── engine.py             # run_backtest() : pas de lookahead (signal barre i exécuté
│   │                         #   à l'open de i+1), FeeModel, slippage, funding/barre, stop/TP
│   │                         #   intrabarre, sizing all_in/risk, vol targeting, stop de DD global
│   ├── metrics.py            # CAGR, Sharpe, Sortino, drawdown max, win rate, profit factor, total_fees
│   ├── optimize.py           # grid_search (score = Sharpe pénalisé par instabilité vs voisins)
│   ├── walkforward.py        # walk_forward : OOS chaîné + rapport vs in-sample
│   ├── montecarlo.py         # bootstrap des rendements des trades (proba de profit, percentiles)
│   └── portfolio.py          # run_portfolio : multi-actifs, parts égales, corrélation
├── analysis/                 # technical.py (score -1..1) + fundamental.py (info yfinance)
├── advice.py                 # avis combiné 65 % tech / 35 % fond -> label + raisons + stop + earnings
├── screener.py               # screen_assets() : backteste TOUTES les stratégies par actif,
│                             #   recommande position + horizon + entrée/stop/objectif ;
│                             #   top_pick() = conseil « quoi acheter aujourd'hui » (bandeau GUI)
└── gui/
    ├── app.py                # Tkinter : 1 barre d'instrument GLOBALE partagée par les 4 onglets
    │                         #   (Analyse / Backtest / Screener / Portefeuille) ; Treeviews triables ;
    │                         #   Backtest : sélecteur de frais (TR FR défaut), vol target, stop DD,
    │                         #   boutons Walk-forward / Grid search / Monte Carlo ; double-clic
    │                         #   screener -> charge actif + stratégie recommandée
    └── chart.py              # chandeliers matplotlib + indicateurs + courbe d'equity
tests/                        # unittest, données synthétiques (tests/helpers.py), aucun réseau
```

## Règles de fonctionnement (à respecter impérativement)

1. **GUI thread-safe** : jamais d'appel Tk depuis un thread worker (ni lecture de widgets).
   Utiliser `TradeHelperApp._run_async(work, on_done)` (queue + polling) et lire les
   widgets UNIQUEMENT dans le thread principal avant de lancer le worker
   (`_ctx_values`). Ne pas toucher à `root.after` depuis un worker.
2. **Pas de lookahead** dans le backtest : signaux calculés sur les barres ≤ i, exécutés
   à l'open de i+1. Test `test_no_lookahead_entry_next_bar` le vérifie.
3. **Pas de réseau dans les tests unitaires** : données synthétiques uniquement
   (monkeypatcher `load_data` si besoin, avec un seed déterministe — ne PAS utiliser
   `hash()` qui change à chaque process).
4. UI et messages en **français**. Pas d'emoji dans le code ni les commits.
5. Couche données : `load_data()` lit d'abord le **store SQLite**, ne télécharge que les
   plages manquantes via la chaîne de fournisseurs (config `providers` par source).
   `normalize()` produit toujours Open/High/Low/Close/Volume, index DatetimeIndex trié naive.
   Ne jamais appeler les providers directement hors de `load_data` (sauf tests avec store temporaire).
6. Avertissement « outil éducatif, pas un conseil en investissement » à maintenir (README).

## Commandes

```bash
pip install --user -r requirements.txt     # pandas numpy matplotlib yfinance requests pillow
python3 run.py                              # lancer la GUI (nécessite un display X)
python3 -m unittest discover -s tests -v   # tests (36 tests, hors-ligne)
```

## État actuel / limites connues

- Forex uniquement en daily (données BCE/Frankfurter) ; fondamental + calendrier earnings
  uniquement pour les actions.
- Historique long : OK en daily/hebdo/mensuel (Yahoo `max`, ex. AAPL depuis 1980 ; Binance
  paginé, ex. BTCUSDT depuis 2017). **Intraday long indisponible** : Yahoo limite
  (1m = 7 j, 5m = 60 j, 1h = 730 j max). Stooq inutilisable (challenge JS anti-bot).
- Screener : une stratégie "best" choisie par Sharpe (repli rendement si < 2 trades).
- Pas encore d'alertes, ni de CI, ni de watchlist persistante (hors store de prix).

## Dépôt git

Dépôt initialisé à la racine du projet (branche `main`). Ne push sur aucun remote sans
autorisation explicite. Voir ROADMAP.md pour le CI/CD et les évolutions prévues.
