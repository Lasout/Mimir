# AGENTS.md — contexte projet tradeHelper

Ce document est la **mémoire de projet** : lis-le avant toute modification.
La feuille de route détaillée est dans [`ROADMAP.md`](ROADMAP.md).

## Qu'est-ce que c'est

Outil d'aide au trading **swing / day trading**, en **Python 3 + Tkinter** (GUI) +
pandas/numpy/matplotlib. Données **ouvertes et gratuites** (pas de clé API) :
- actions/ETF : Yahoo Finance via `yfinance`
- crypto : API publique Binance (klines)
- forex : taux quotidiens BCE via `frankfurter.dev`

Cache SQLite local dans `~/.local/share/tradehelper/market.db` (+ snapshots Parquet),
TTL par intervalle, repli automatique hors-ligne. Yahoo rate-limite parfois (429) :
le store sert de repli.

## Architecture

```
run.py                        # point d'entrée GUI : python3 run.py
tradehelper/
├── config.py                 # configuration (défauts < .env < JSON $TRADEHELPER_CONFIG < env ;
│                             #   .env parsé sans dépendance, jamais commité — clés API)
│                             #   dont adjust_prices=True (défaut, vérifié en backtest) : changer
│                             #   le flag vide le store (prix bruts vs ajustés incompatibles)
├── compute.py                # backend de calcul : auto/cpu/cuda (cupy GPU + xgboost CUDA),
│                             #   détection GPU, add_all_gpu (équivalent cupy de add_all),
│                             #   repli CPU automatique ; vérifier avec `python3 -m tradehelper.compute`
│                             #   GPU : pip install -r requirements-gpu.txt (CUDA >= 12.8 pour RTX 50)
├── timeutil.py               # utcnow_naive() (pandas 3.x)
├── data/
│   ├── store.py              # Store SQLite (~/.local/share/tradehelper/market.db) :
│   │                         #   tables bars + meta (upsert incrémental, thread-safe)
│   │                         #   + watchlist (actifs suivis) + runs (historique des
│   │                         #   analyses) + paper_positions + journal (P3) ;
│   │                         #   meta sert aussi aux prefs UI (ui.theme,
│   │                         #   ui.font_scale, ui.lang) et à la trésorerie paper
│   │                         #   (paper.cash) ; cache Parquet read-through
│   │                         #   dans parquet/ à côté du .db (si pyarrow, sinon SQLite) :
│   │                         #   snapshot COMPLET réécrit à chaque upsert + garde-fou de
│   │                         #   comptage (snapshot < SQLite -> repli SQLite)
│   ├── providers.py          # chaîne de fournisseurs : Yahoo, Binance (paginé 1000/appel),
│   │                         #   Frankfurter (forex daily), Alpha Vantage (si clé env)
│   ├── sources.py            # load_data(...) : sert depuis le store, ne télécharge que les
│   │                         #   plages manquantes (tête « max » sondée 1x/24h + queue fraîche
│   │                         #   selon TTL) ; périodes : 1mo..5y + max
│   ├── events.py             # calendrier : earnings (yfinance) + réunions BCE/FOMC
│   │                         #   (scraping pages officielles, parsers purs testés,
│   │                         #   cache 24 h dans meta) ; affiché dans l'avis si < 14 j
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
├── analysis/
│   ├── technical.py          # score technique -1..1 (tendance, momentum, volatilité)
│   ├── fundamental.py        # info yfinance (PER vs secteur, PEG, marges, dette, avis...)
│   │                         #   + intègre advanced.py (composantes pondérées, non bloquantes)
│   └── advanced.py           # fondamental avancé : surprise de BPA (earnings_dates),
│                             #   valorisation relative au secteur (médianes de pairs),
│                             #   sentiment news RSS (lexique fr/en) — fetchers injectables,
│                             #   résultats cachés dans le store (TTL 6–24 h)
├── advice.py                 # avis combiné 65 % tech / 35 % fond -> label + raisons + stop + earnings
├── screener.py               # screen_assets(backend=...) : backteste TOUTES les stratégies par
│                             #   actif ; recommande position + horizon + entrée/stop/objectif ;
│                             #   backend cpu -> pool de processus, cuda -> indicateurs GPU + ML CUDA ;
│                             #   top_pick() = conseil « quoi acheter aujourd'hui » (bandeau GUI)
├── paper.py                  # PaperPortfolio (P3) : portefeuille fictif PERSISTANT (store),
│                             #   open/close avec FeeModel, mark/stats, apply_screener (suit un
│                             #   scan : ouvre sur ACHAT, clôture sur stop/objectif/signal)
├── broker.py                 # brokers (P3) : PaperBroker (refuse dry_run=False) + IBKRBroker
│                             #   verrouillé (env TRADEHELPER_ALLOW_LIVE_TRADING=1 + config +
│                             #   confirmation + plafond max_order_notional) — aucun ordre réel
│                             #   sans validation explicite
└── gui/
    ├── app.py                # Tkinter : 1 barre d'instrument GLOBALE partagée par les 5 onglets
    │                         #   (Analyse / Backtest / Screener / Portefeuille / Paper & journal) ;
    │                         #   Treeviews triables ;
    │                         #   Backtest : sélecteur de frais (TR FR défaut), vol target, stop DD,
    │                         #   boutons Walk-forward / Grid search / Monte Carlo ; double-clic
    │                         #   screener -> charge actif + stratégie recommandée ;
    │                         #   onglet Paper (Ctrl+5) : sync avec le scan, ouverture depuis l'avis,
    │                         #   clôture manuelle, reset, journal ; menubar + raccourcis
    │                         #   (Ctrl+1..5/R/B/S/D/H/E, F1), watchlist persistante (onglet
    │                         #   Screener), historique des runs (Ctrl+H)
    ├── theme.py              # palettes clair/sombre (ttk clam + rcParams matplotlib) + zoom
    │                         #   police ; partie pure testée hors-ligne (tests/test_theme.py)
    ├── i18n.py               # dictionnaire fr/en (fr par défaut — règle projet), get_lang()
    │                         #   (env TRADEHELPER_LANG), switch à chaud via set_language()
    ├── export.py             # exports PNG (Figure) / CSV (screener, trades) / TXT — fonctions pures
    └── chart.py              # chandeliers matplotlib + indicateurs + courbe d'equity +
                              #   PriceCrosshair (réticule + infobulle OHLC au survol)
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
7. **Jamais d'ordres réels sans validation explicite** : le broker par défaut est `paper`
   (`broker.py`) ; `IBKRBroker` exige `TRADEHELPER_ALLOW_LIVE_TRADING=1` + config complète +
   `confirm=True` + notionnel ≤ `max_order_notional`.

## Commandes

```bash
pip install --user -r requirements.txt     # pandas numpy matplotlib yfinance requests pillow
python3 run.py                              # lancer la GUI (nécessite un display X)
python3 -m unittest discover -s tests -v   # idem, compatible pytest :
python3 -m pytest                          # suite pytest (hors-ligne, sans display)
python3 -m pytest --cov=tradehelper.indicators --cov=tradehelper.backtest \
  --cov=tradehelper.strategies --cov=tradehelper.screener --cov-fail-under=80
                                            # couverture du cœur (93 %, objectif > 80 %)
ruff check . && ruff format --check .      # lint (config pyproject.toml)
pre-commit run --all-files                  # hooks : ruff, whitespace, yaml, detect-secrets
                                            #   (baseline .secrets.baseline) — installer avec
                                            #   pip install -r requirements-dev.txt && pre-commit install
TRADEHELPER_NETWORK_TESTS=1 python3 -m unittest tests.test_integration_network -v
                                            # tests d'intégration RÉSEAU (marqués, skippés
                                            #   sinon — ne JAMAIS les lancer sans le flag)
python3 -m mypy                             # typage strict du cœur (pyproject.toml :
                                            #   backtest, strategies, screener ; le reste en
                                            #   ignore_errors) — P5
# CI/CD : .github/workflows/ci.yml (tests multi-Python + mypy + gitleaks),
#   network.yml (workflow_dispatch), release.yml (tags v* -> PyInstaller multi-OS)
# packaging : pyproject.toml (pip install . -> tradehelper-gui), packaging/tradehelper.spec
#   (pyinstaller), CHANGELOG.md (Keep a Changelog), .env.example (clés, .env gitignoré)
# doc utilisateur : docs/guide-utilisateur.md (mkdocs.yml pour publication)
```

## État actuel / limites connues

- Forex uniquement en daily (données BCE/Frankfurter) ; fondamental + calendrier earnings
  uniquement pour les actions.
- Historique long : OK en daily/hebdo/mensuel (Yahoo `max`, ex. AAPL depuis 1980 ; Binance
  paginé, ex. BTCUSDT depuis 2017). **Intraday long indisponible** : Yahoo limite
  (1m = 7 j, 5m = 60 j, 1h = 730 j max). Stooq inutilisable (challenge JS anti-bot).
- Screener : une stratégie "best" choisie par Sharpe (repli rendement si < 2 trades).
- UI : thème clair/sombre + taille de police + langue (fr/en, fr par défaut) persistés dans
  `meta` ; watchlist, historique des runs, paper trading et journal dans le store SQLite ;
  barre de progression pendant les calculs ; chargement lazy (mémo LRU des indicateurs) +
  cache Parquet. CI/CD en place (tests + mypy strict + gitleaks ; releases v* avec
  PyInstaller). Alertes non faites (hors périmètre demandé).

## Cahier des charges supplémentaire (cadsup.md)

`cadsup.md` : moteur paper conforme — remplissage au open de J+1 (jamais au prix du
signal), coûts non négociables (frais ≥ 0,1 % AR, slippage contre la position), accounting
en multiples de R — audit anti-biais en tests automatisés (look-ahead, survivorship,
cherry-picking, overfitting, régime, significativité ≥ 100 trades), gouvernance (version
figée), grille go/no-go capital réel. Plan détaillé dans ROADMAP.md section **P6**.
Toute statistique produite par l'outil doit être lue à travers ces règles : pas de
métrique de référence sans coûts, pas de conclusion < 100 trades, look-ahead interdit.

## Dépôt git

Dépôt initialisé à la racine du projet (branche `main`), remote `origin` =
`https://github.com/Lasout/Mimir.git` (repo **public**, licence MIT, `main` protégée :
PR + checks CI requis). Ne push/merge sur `main` ni aucun tag `v*` sans autorisation
explicite (le tag déclenche la release PyInstaller). `gh` est authentifié dans le sandbox
(protocole HTTPS) : `git push` fonctionne via le credential helper gh.
CI/CD actif (`.github/workflows/`) ; évolutions prévues dans ROADMAP.md.
Protection de `main` (require PR + checks CI) à activer dans les settings GitHub.
