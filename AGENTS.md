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
├── data/
│   ├── sources.py            # load_data(source, symbole, période, intervalle) + cache CSV
│   └── universe.py           # listes d'actifs connus par source (actions/crypto/forex)
├── indicators.py             # SMA/EMA, RSI Wilder, MACD, Bollinger, ATR, ADX, Donchian, OBV, VWAP...
├── strategies/               # registre STRATEGIES : ma_cross, rsi_reversion,
│                             #   donchian_breakout, macd_momentum (stop ATR intégré)
├── backtest/
│   ├── engine.py             # run_backtest() : pas de lookahead (signal barre i exécuté
│   │                         #   à l'open de i+1), commission, slippage, stop/TP intrabarre,
│   │                         #   sizing all_in ou risk (ATR)
│   └── metrics.py            # CAGR, Sharpe, Sortino, drawdown max, win rate, profit factor...
├── analysis/                 # technical.py (score -1..1) + fundamental.py (info yfinance)
├── advice.py                 # avis combiné 65 % tech / 35 % fond -> label + raisons + stop
├── screener.py               # screen_assets() : backteste TOUTES les stratégies par actif,
│                             #   recommande position + horizon + entrée/stop/objectif
└── gui/
    ├── app.py                # Tkinter : 1 barre d'instrument GLOBALE (source/actif/période/
    │                         #   intervalle) partagée par les 3 onglets (Analyse / Backtest /
    │                         #   Screener) ; Treeviews triables (clic en-tête) ; double-clic
    │                         #   screener -> charge actif + stratégie recommandée
    └── chart.py              # chandeliers matplotlib + indicateurs + courbe d'equity
tests/                        # unittest, données synthétiques (tests/helpers.py), aucune réseau
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
5. Ne casse pas le cache : `_normalize()` produit toujours Open/High/Low/Close/Volume,
   index DatetimeIndex trié naive.
6. Avertissement « outil éducatif, pas un conseil en investissement » à maintenir (README).

## Commandes

```bash
pip install --user -r requirements.txt     # pandas numpy matplotlib yfinance requests pillow
python3 run.py                              # lancer la GUI (nécessite un display X)
python3 -m unittest discover -s tests -v   # tests (36 tests, hors-ligne)
```

## État actuel / limites connues

- Forex uniquement en daily (données BCE) ; fondamental uniquement pour les actions.
- Historique intraday Yahoo limité (ex. 1m = 7 jours max) ; Binance paginé à 1000 barres/appel.
- Screener : une stratégie "best" choisie par Sharpe (repli rendement si < 2 trades).
- Pas encore de persistance (watchlist, historique backtests), ni d'alertes, ni de CI.

## Dépôt git

Dépôt initialisé à la racine du projet (branche `main`). Ne push sur aucun remote sans
autorisation explicite. Voir ROADMAP.md pour le CI/CD et les évolutions prévues.
