# ROADMAP — tradeHelper

Feuille de route de développement. Les cases cochées sont faites.
Contexte projet pour agents : voir [`AGENTS.md`](AGENTS.md).
Cahier des charges supplémentaire (paper trading conforme + audit anti-biais + go/no-go)
: voir [`cadsup.md`](cadsup.md) — traduit en plan dans la **P6** ci-dessous.

## P0 — fondations (fait)

- [x] Dépôt git initialisé à la racine (`main`, commit initial, `.gitignore`)
- [x] Remote GitHub ajouté et `main` pushé : `origin` = `https://github.com/Lasout/Mimir.git`
      (dépôt **public**, licence MIT). `main` protégée côté GitHub (PR + checks requis).
      **Ne push/merge sur `main` que sur autorisation.**
- [x] CI/CD GitHub Actions (`.github/workflows/ci.yml`) : lint `ruff` (check + format),
      tests pytest + couverture sur chaque push/PR, matrice Python 3.11–3.15,
      mypy strict, gitleaks ; badges dans le README. `network.yml` (tests réseau, manuel),
      `release.yml` (tags `v*`)
- [x] Hooks pre-commit (`.pre-commit-config.yaml`) : ruff check --fix + ruff-format,
      trailing-whitespace, end-of-file, check-yaml, detect-secrets (baseline
      `.secrets.baseline`) — `pre-commit run --all-files` : tous les hooks passent
- [x] Tri asc/desc des colonnes du screener (clic sur l'en-tête) — étendre au tableau des trades
- [x] Tests sous `pytest` + `pytest-cov` (config `pyproject.toml`) : **93 %** de couverture
      sur `indicators`, `backtest`, `strategies`, `screener` (objectif > 80 %, CI
      `--cov-fail-under=80`) ; la suite reste compatible `unittest discover`
- [x] `pyproject.toml` + packaging pip installable (`pip install .` → `tradehelper-gui`),
      exécutable autonome via PyInstaller (`packaging/tradehelper.spec`)

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
      indéterminée animée sinon). Screener **verbeux** : la ligne affiche la phase en cours
      (« chargement des données », « indicateurs + avis », « backtest <stratégie> (k/N) »)
      + symbole + écoulé + ETA, même pendant les étapes longues (téléchargement, avis).
      Les **ETF ne déclenchent plus d'appels réseau fondamentaux/earnings** (plus de
      bruit « No earnings / 404 / delisted » de yfinance) : fondamental et calendrier
      des résultats sont réservés aux actions.
- [x] **Performance** : screener parallélisé (pool de processus, P2), **chargement lazy**
      (mémo LRU des DataFrames indicateurs dans la GUI), **cache Parquet** read-through
      dans le store (`parquet/` à côté du .db, si pyarrow installé, repli SQLite sinon)
- [x] **Documentation utilisateur** : `docs/guide-utilisateur.md` + `mkdocs.yml`
      (tutoriel « premier backtest », onglets, raccourcis, GPU, config)

## P5 — qualité & distribution (fait)

- [x] Typage strict : `pyproject.toml` + `python3 -m mypy` — `strict = true` sur le cœur
      (`tradehelper.backtest`, `tradehelper.strategies`, `tradehelper.screener`),
      modules périphériques (gui, data, providers, analyse) en typage progressif
      (`ignore_errors` local) ; CI mypy sur PR/push
- [x] Tests d'intégration avec données réelles samplées (`tests/test_integration_network.py`),
      marqués et **skippés par défaut** : `TRADEHELPER_NETWORK_TESTS=1` pour les lancer
      (CI rapide hors-ligne, job `network.yml` en `workflow_dispatch`)
- [x] Releases GitHub : tags sémantiques `v*` -> workflow `release.yml` (PyInstaller
      multi-OS via `packaging/tradehelper.spec`, release avec artifacts),
      `CHANGELOG.md` (Keep a Changelog), `pyproject.toml` (package installable,
      entry point `tradehelper-gui`)
- [x] Sécurité : clés en variables d'environnement + fichier `.env` (chargé par
      `config.py` sans dépendance, **non commité** — `.gitignore` + `.env.example`),
      scan de secrets gitleaks en CI (`ci.yml`)

## P6 — paper trading conforme & audit anti-biais (cahier des charges : `cadsup.md`)

Spec de référence : [`cadsup.md`](cadsup.md) (moteur paper « comme un ordre réel » + audit
anti-biais en tests automatisés + gouvernance + grille go/no-go capital réel).
Légende : **(déjà en place)** = existe, à consolider ; le reste est à construire.

### P6.1 — Moteur paper conforme cadsup §2 (remplissage J+1, coûts, R-multiples)

- [ ] **Remplissage au open de J+1, jamais au prix du signal** : le conseil du screener est
      produit à la clôture de J avec les données ≤ J ; le fill simulé se fait à l'open de
      J+1 + slippage. *(Le backtest applique déjà « signal J → exécution open J+1 »
      (test `test_no_lookahead_entry_next_bar`) ; le paper actuel remplit au prix courant
      -> à refondre sur le même principe.)*
- [ ] **Cycle quotidien idempotent** : rejouer le même jour ne crée pas de doublon
      (clé `trade_id` = hash(strategy_version, asset, signal_date)) ; horodatage à
      l'horloge réelle uniquement
- [ ] **Convention stop prioritaire** si stop et cible sont touchés le même jour
- [ ] **Journal par trade** conforme au schéma cadsup §2.4 : `trade_id`,
      `strategy_version`, `signal_date`, `asset`, `direction`, `planned_entry`, `stop`,
      `target`, `risk_pct`, `fill_price`, `fill_date`, `exit_*`, `fees`, `r_planned`,
      `r_realized` (R = P&L / risque initial au stop, **net de frais et slippage**)
      — migration de la table `paper_positions` actuelle
- [ ] **Modèle de coûts non négociable** : frais aller-retour ≥ 0,1 % du notionnel
      (configurable, min garde-fou), slippage fixe 0,05 %/côté ou basé sur la volatilité
      récente, appliqué **contre** la position à l'entrée et à la sortie. Aucune métrique
      de référence sans coûts (bouton « bruts » autorisé mais jamais affiché comme référence)
- [ ] **Tableau de bord paper** (trades clôturés uniquement) : espérance en R (cible
      +0,15R..+0,30R), win rate **et** payoff moyen ensemble, max drawdown en R et en %,
      + benchmark **buy-and-hold/DCA des mêmes actifs sur la même fenêtre** + n trades
      vs win rate d'équilibre (1/(1+payoff))

### P6.2 — Versionnage & gouvernance cadsup §4

- [ ] **Version de stratégie figée** : `strategy_version` = hash gelé du jeu de paramètres
      (ex. semver + hash) ; toute modif de paramètre = nouvelle version et **remise à zéro
      du compteur d'échantillon**
- [ ] **Carnet d'idées séparé** (notes sans appliquer à la version en cours)
- [ ] Capital simulé, risque/trade (ex. 1 %) et modèle de coûts **fixés en début de test**
- [ ] **Aucun décrochage manuel** : tout signal émis est journalisé et « tradé »
      virtuellement, même absurde
- [ ] Revue à chaque palier de 100 trades clôturés (événement dans l'historique des runs)

### P6.3 — Audit anti-biais en tests automatisés cadsup §3

- [ ] **Look-ahead (priorité absolue)** : test de non-régression par troncature — 20 jours
      historiques aléatoires (seed fixe), tronquer à J, recalculer le conseil, vérifier
      l'identité avec le conseil « d'époque » ; `shift()` systématique ; pas de
      resample/merge sur l'ensemble complet avant split train/test ; chasse aux indicateurs
      « repaint » (ZigZag, fractales, Supertrend recalculé). *(Partiel : test
      `test_no_lookahead_entry_next_bar` existe sur le backtest ; à étendre à l'avis/screener
      et au remplissage J+1.)*
- [ ] **Survivorship bias** : l'univers éligible au jour J est celui connu à J (crypto :
      inclure disparus/illiquides) ; test : aucune info postérieure (delisting, cap/volume
      actuels) dans le filtre historique
- [ ] **Cherry-picking** : test — nombre de lignes du journal == nombre de signaux émis
      par version de stratégie (aucune exclusion rétrospective)
- [ ] **Overfitting** : split temporel train/test + walk-forward existants ; ajouter :
      alerte « trop de paramètres » (score pénalisé déjà dans `grid_search`, à afficher)
      et test de version gelée (changer un paramètre -> nouvelle `strategy_version`)
- [ ] **Régime de marché** : taguer chaque métrique du régime de la période (haussier/plat/
      baissier via tendance d'un indice de référence, ex. SPY/BTC) ; aucune conclusion
      préliminaire hors échantillon couvrant ≥ 2 régimes
- [ ] **Significativité** : seuil ≥ 100 trades clôturés par version avant toute conclusion ;
      afficher l'intervalle de confiance de l'espérance (± 2·σ_R/√n) — si l'IC contient 0,
      afficher « on ne sait rien »

### P6.4 — Grille go/no-go capital réel cadsup §5

- [ ] Page/panneau « go/no-go » calculant les 6 critères **simultanément** : ≥ 100 trades
      clôturés ; espérance nette > +0,15 R avec IC excluant 0 ; payoff ≥ 1,5 ou win rate
      ≥ 45 % (avec espérance positive) ; max DD ≤ 20–25 % ; perf nette > buy-and-hold des
      mêmes actifs ; échantillon ≥ 2 régimes. Un seul manquant = « on continue le paper ».
- [ ] Le déblocage du broker réel (IBKR) reste verrouillé tant que la grille n'est pas
      verte **et** que `TRADEHELPER_ALLOW_LIVE_TRADING=1` + confirmation + plafond
      ne sont pas réunis (garde-fous existants, à chaîner avec la grille)

### Ordre de réalisation proposé

1. P6.3 look-ahead (tests) — la fiabilité de la mesure est la partie 1 du chantier
2. P6.1 moteur paper conforme (fill J+1, coûts, journal R, idempotence)
3. P6.1 dashboard + benchmark buy-and-hold
4. P6.2 versionnage & gouvernance
5. P6.3 reste des audits (survivorship, cherry-picking, régime, significativité)
6. P6.4 grille go/no-go + chaînage avec le broker

## Idées à creuser (non priorisées)

- Backtest sur données tick/level-2 (Binance trades) pour le day trading
- Corrélation cross-asset (ex. BTC vs NASDAQ) dans l'onglet Analyse
- Alertes de cassure de range en temps réel (websocket Binance)
- Comparaison de stratégies côte à côte (equity curves superposées)
