# ROADMAP — tradeHelper

Feuille de route de développement. Les cases cochées sont faites.
Contexte projet pour agents : voir [`AGENTS.md`](AGENTS.md).

## P0 — fondations (fait / à finaliser)

- [x] Dépôt git initialisé à la racine (`main`, commit initial, `.gitignore`)
- [ ] Ajouter un remote (GitHub/GitLab) et protéger `main` — **ne push que sur autorisation**
- [ ] CI/CD GitHub Actions : lint (`ruff`), tests unitaires sur chaque push/PR,
      matrice de versions Python (3.11–3.15), badge de build dans le README
- [ ] Hooks pre-commit (ruff, trailing whitespace, pas de secrets en dur)
- [x] Tri asc/desc des colonnes du screener (clic sur l'en-tête) — étendre au tableau des trades
- [ ] Migrer les tests vers `pytest` + couverture (`pytest-cov`, objectif > 80 % sur
      `indicators`, `backtest`, `strategies`, `screener`)
- [ ] `pyproject.toml` + packaging pip installable (`pip install .`), puis exécutable
      autonome via PyInstaller

## P1 — données : historique plus long et persistant (fait)

- [x] **Historique long** : période `max` ; pagination Binance en boucle (1000 klines/appel,
      plafond `max_bars_per_fetch`) ; Yahoo `period="max"` pour actions daily/hebdo/mensuel
- [x] **Base SQLite** à la place du cache CSV : `~/.local/share/tradehelper/market.db`,
      upsert incrémental (tête sondée 1×/24 h, queue selon TTL), requêtes par symbole/intervalle,
      + cache Parquet read-through (snapshot complet + garde-fou de comptage, P4)
- [x] **Fournisseurs configurables** avec fallback : `config.providers` (défaut
      stock: yahoo→alphavantage, crypto: binance→yahoo, forex: frankfurter→yahoo) ;
      Alpha Vantage si `ALPHAVANTAGE_API_KEY` ; config via `TRADEHELPER_CONFIG` (JSON) + env
- [x] **Calendrier des annonces** : date des résultats (yfinance `calendar`), cachée 24 h
      dans le store, affichée dans l'avis avec alerte si < 14 j
- [x] **Ajustement des prix (dividendes/splits)** : `adjust_prices` (Yahoo `auto_adjust`),
      **activé par défaut** après vérification de l'impact backtest (KO 5 ans, ma_cross :
      +29,4 %/Sharpe 0,48/DD −17,1 % ajusté vs +6,6 %/0,17/−22,4 % brut). Changer le flag
      vide proprement le store (`clear_bars`, parquet compris) et re-télécharge ; une base
      legacy sans méta est considérée comme brute
- [x] **Calendrier BCE/Fed** : scraping des pages officielles (federalreserve.gov FOMC,
      ecb.europa.eu Governing Council), parsers purs testés hors-ligne, jours de décision
      uniquement, cache 24 h dans le store, affiché dans l'avis avec alerte si < 14 j
- [x] Stooq : abandonné — challenge JS anti-bot, documenté, ne pas réessayer sans navigateur

## P2 — backtest & stratégies (niveau pro)

- [x] **Frais réalistes** : `fees.py` — `FeeModel` (fixe + % + min/max **par exécution**),
      préréglages dont **Trade Republic FR par défaut** (1 €/ordre actions/ETF,
      1 % min 1 € max 50 € crypto) ; sélecteur dans l'onglet Backtest et Portefeuille
- [x] **Walk-forward** (`backtest/walkforward.py`) : blocs rolling/ancrés, grid search sur
      le train, equity OOS chaînée, rapport OOS vs in-sample (mesure d'overfitting)
- [x] **Grid search** (`backtest/optimize.py`) avec garde-fou anti-overfitting :
      score = Sharpe − pénalité d'instabilité vs configurations voisines
- [x] **Monte Carlo** (`backtest/montecarlo.py`) : bootstrap des rendements des trades,
      proba de profit, percentiles de valeur finale et de drawdown
- [x] **Portefeuille multi-actifs** (`backtest/portfolio.py` + onglet GUI) : parts égales
      entre actifs en position, frais par exécution, matrice de corrélation
- [x] **Risk management moteur** : vol targeting (réduit la taille si vol > cible),
      stop de drawdown global avec hystérésis, **funding par barre** (perps crypto)
- [x] **Régime adaptatif** (`adaptive_regime`) : ADX > seuil → suivi de tendance (EMA rapide
      vs lente, state-based) ; sinon mean-reversion RSI
- [x] **Signal ML** (`ml_signal`) : HistGradientBoosting sklearn sur 9 features techniques
      **causales**, réentraînement périodique sur fenêtre glissante (pas de lookahead),
      CV temporelle `TimeSeriesSplit` exposée en diagnostic (`val_accuracy`)
- [x] **Sizing de Kelly fractionnaire** : `sizing="kelly"` dans le moteur (f = W − (1−W)/R
      sur les trades passés, × `kelly_fraction`, plafonné à 95 % du capital)
- [x] **Limite de corrélation du portefeuille** : `max_corr` dans `run_portfolio`
      (un actif trop corrélé aux positions déjà retenues est écarté)
- [x] **Conseil « quoi acheter aujourd'hui »** : `top_pick()` sur les résultats du screener
      (meilleur candidat ACHAT, rang = 0,6 × score d'avis + 0,4 × Sharpe normalisé),
      affiché dans un bandeau dédié de l'onglet Screener
- [x] **ETF ajoutés à l'univers** (SPY, QQQ, VOO, VTI, SCHD, JEPI, VWCE.DE, IWDA.AS,
      EQQQ.PA, CSPX.L, CW8.PA...)
- [x] **ETF visibles par défaut dans le screener et le portefeuille** : listes par défaut
      mixtes actions + ETF (`default_symbols()`, ~1/3 d'ETF) + colonne « type »
      (Action/ETF/Crypto/Forex) dans le tableau du screener
- [x] **Mode calcul GPU optionnel** (`compute.py`, `requirements-gpu.txt`) : backend
      `auto`/`cpu`/`cuda` (config + env `TRADEHELPER_BACKEND` + sélecteur GUI) ; en CUDA,
      indicateurs via cupy (`add_all_gpu`, test d'équivalence GPU vs CPU exécuté seulement
      sur machine NVIDIA) et ML via XGBoost `device="cuda"` ; en CPU, screener parallélisé
      en pool de processus. Repli CPU automatique et silencieux si pas de GPU.
      Note : les RTX 50 (Blackwell) demandent CUDA >= 12.8 (cupy-cuda12x >= 13.6).

## P3 — alertes, paper trading, exécution

- [ ] Alertes sur signaux (Telegram/Discord via webhook, email) avec niveaux entrée/stop/objectif
  — **exclues du périmètre demandé pour l'instant**
- [x] **Paper trading** (`paper.py`, tables SQLite `paper_positions` + meta `paper.cash`) :
      portefeuille fictif **persistant** (trésorerie + positions ouvertes survivent aux
      redémarrages), frais via le modèle de frais actif (TR FR par défaut), ouverture
      manuelle depuis l'avis (Ctrl+R) ou **synchronisation avec un scan** du screener
      (`apply_screener` : ouvre sur signal ACHAT, clôture sur signal non-ACHAT / stop /
      objectif touché, respecte `max_positions`) ; onglet dédié « Paper trading & journal »
      (Ctrl+5) avec positions ouvertes, trades clôturés et métriques (equity, P&L latent/réalisé,
      win rate)
- [x] Connexion broker (`broker.py`) — **avec garde-fous** : `PaperBroker` par défaut
      (refuse `dry_run=False`), `IBKRBroker` verrouillé tant que
      `TRADEHELPER_ALLOW_LIVE_TRADING=1` + config `ibkr` complète + `ib_insync` installé +
      confirmation explicite + plafond `max_order_notional`. Ne jamais exécuter d'ordres
      réels sans validation explicite
- [x] Journal de trading (table SQLite `journal` : symbole, type note/entrée/sortie/idée,
      texte, capture d'écran) dans le même onglet, CRUD complet

## P4 — analyse & UI/UX (fait)

- [x] **Fondamental avancé** (`analysis/advanced.py`, fetchers injectables + cache store) :
      **surprise de BPA** (yfinance `get_earnings_dates`, (publié−consensus)/|consensus|),
      **valorisation relative au secteur** (médianes PER/marges de 8 pairs de même secteur),
      **sentiment news** (flux RSS Yahoo, lexique fr/en) — fondu dans le score fondamental
- [x] **Watchlist persistante** (table SQLite `watchlist`, panneau dans l'onglet Screener :
      ajouter l'actif courant / retirer / charger) + **historique des runs** (table
      `runs` : screener/backtest/portefeuille loggés avec métriques, dialog Ctrl+H)
- [x] **Exports** : PNG du graphique actif (Ctrl+E), CSV du screener (Ctrl+Shift+S) et des
      trades, TXT du rapport affiché — via `gui/export.py` (fonctions pures testées)
- [x] **Graphique interactif** : réticule + infobulle OHLC/volume/RSI au survol
      (`PriceCrosshair` dans `gui/chart.py`, sans dépendance externe) ; **thème sombre**
      (Ctrl+D, persisté, ttk clam + rcParams matplotlib) ; zoom de police (Ctrl++/Ctrl+-)
- [x] **Accessibilité clavier & raccourcis** : menubar (Fichier/Onglets/Affichage/Aide),
      Ctrl+1..4, Ctrl+R/B/S, Ctrl+H, F1 (dialog d'aide), infobulles sur les boutons
- [x] **i18n fr/en** (`gui/i18n.py`, défaut **fr** — règle projet ; `TRADEHELPER_LANG` ou
      menu Affichage, switch à chaud + persisté `ui.lang`)
- [x] **Barre de progression** pendant tous les calculs (déterminée x/N pour le screener,
      indéterminée animée sinon)
- [x] **Performance** : screener parallélisé (pool de processus, P2), **chargement lazy**
      (mémo LRU des DataFrames indicateurs dans la GUI), **cache Parquet** read-through
      dans le store (`parquet/` à côté du .db, si pyarrow installé, repli SQLite sinon)
- [x] **Documentation utilisateur** : `docs/guide-utilisateur.md` + `mkdocs.yml`
      (tutoriel « premier backtest », onglets, raccourcis, GPU, config)

## P5 — qualité & distribution

- [ ] Typage strict (`mypy --strict` sur le cœur : backtest, strategies, screener)
- [ ] Tests d'intégration avec données réelles samplées (marquées `network`, exclues du CI rapide)
- [ ] Releases GitHub (tags sémantiques), changelog, paquets PyInstaller multi-OS
- [ ] Sécurité : variables d'environnement pour les clés (`.env` non commité), scan de secrets

## Idées à creuser (non priorisées)

- Backtest sur données tick/level-2 (Binance trades) pour le day trading
- Corrélation cross-asset (ex. BTC vs NASDAQ) dans l'onglet Analyse
- Alertes de cassure de range en temps réel (websocket Binance)
- Comparaison de stratégies côte à côte (equity curves superposées)
