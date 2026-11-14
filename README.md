# tradeHelper

> Pour les agents qui reprennent le projet : lire d'abord [`AGENTS.md`](AGENTS.md)
> (architecture, règles, commandes). La feuille de route est dans [`ROADMAP.md`](ROADMAP.md).

Outil d'aide au trading orienté **swing / day trading**, basé sur des **données ouvertes**
(gratuites, sans clé API) et combinant **analyse technique** et **analyse fondamentale**.

## Fonctionnalités

- **Données ouvertes** : actions/ETF via Yahoo Finance, cryptos via l'API publique Binance
  (paginé, historique complet depuis 2017), forex via les taux quotidiens de la BCE
  (frankfurter.dev), avec **fallback automatique** (Yahoo sert aussi `BTC-USD`, `EURUSD=X` ;
  Alpha Vantage si `ALPHAVANTAGE_API_KEY` est défini).
- **Stockage persistant SQLite** (`~/.local/share/tradehelper/market.db`) : mise à jour
  **incrémentale** (seules les plages manquantes sont téléchargées ; la tête « max » est
  sondée une fois par 24 h, la queue selon un TTL par intervalle). Période `max` supportée
  (ex. AAPL depuis 1980, BTCUSDT depuis 2017). Hors-ligne : sert la dernière version en base.
- **Configuration** : `tradehelper/config.py`, surchargeable par fichier JSON pointé par
  `TRADEHELPER_CONFIG` et par variables d'environnement (`TRADEHELPER_DB_PATH`,
  `ALPHAVANTAGE_API_KEY`, `TRADEHELPER_ADJUST_PRICES`).
- **Calendrier des annonces** : date des prochains résultats (actions), cachée 24 h et
  affichée dans l'avis avec une alerte si < 14 jours.
- **Analyse technique** : SMA/EMA, RSI (Wilder), MACD, bandes de Bollinger, ATR, ADX,
  canal de Donchian, OBV, VWAP, volatilité rolling.
- **Backtesting pro** : moteur sans lookahead (signal de la barre *i* exécuté à l'open de
  *i+1*), slippage, stop-loss / take-profit en intrabarre, sizing « all-in » ou
  proportionnel au risque (ATR), **vol targeting**, **stop de drawdown global** avec
  hystérésis, **funding par barre** (perps crypto).
- **Frais réalistes** : modèles de frais **par exécution** (fixe + % + min/max) avec
  **Trade Republic France par défaut** (1 €/ordre sur actions/ETF, 1 % min 1 € max 50 €
  sur crypto), plus Binance spot, « sans frais », etc. — sélectionnable dans l'UI.
- **Validation** : walk-forward (OOS chaîné + comparaison in-sample = mesure
  d'overfitting), grid search avec pénalité d'instabilité (score vs voisins),
  Monte Carlo sur les trades (proba de profit, percentiles de valeur finale/DD).
- **Portefeuille multi-actifs** : onglet dédié, répartition à parts égales entre les
  actifs en position, frais réels, matrice de corrélation des rendements.
  Métriques : rendement, CAGR, Sharpe, Sortino, drawdown max, Calmar, win rate,
  profit factor, expectancy, exposition, frais totaux.
- **Stratégies prêtes à l'emploi** :
  - `ma_cross` — croisement d'EMA (swing), filtre de tendance optionnel, stop ATR
  - `rsi_reversion` — mean-reversion sur RSI (day/swing)
  - `donchian_breakout` — breakout sur canal de Donchian + confirmation de volume (swing)
  - `macd_momentum` — momentum MACD (day/swing)
  - `adaptive_regime` — détection de régime par ADX : tendance (EMA rapide vs lente) ou range (RSI)
  - `ml_signal` — classifieur sklearn (HistGradientBoosting) sur 9 features techniques causales,
    réentraînement périodique sur fenêtre glissante, CV temporelle en diagnostic
- **Sizing avancé** : Kelly fractionnaire (`sizing="kelly"`), risque fixe par trade (ATR).
- **Univers d'actifs** : actions US/Europe **+ ETF** (SPY, QQQ, VWCE.DE, IWDA.AS, CW8.PA…),
  crypto, forex — liste déroulante éditable, saisie libre possible.
- **Conseil du jour** : après un screener, un bandeau indique **quoi acheter aujourd'hui**
  (meilleur candidat en position ACHAT : actif, entrée, stop, objectif, horizon, stratégie).
- **Mode calcul GPU optionnel** : sélecteur « Calcul : auto / cpu / cuda » dans la barre du haut
  (aussi `TRADEHELPER_BACKEND` en env). En `cuda` : indicateurs sur GPU via **cupy** et ML
  via **XGBoost CUDA** — utile pour accélérer le screener sur une carte NVIDIA
  (ex. RTX 5060 Ti 16 Go ; les RTX 50 nécessitent CUDA ≥ 12.8). En `cpu` : le screener
  parallélise les actifs sur plusieurs processus. Sans GPU, repli CPU automatique.
  Installation GPU : `pip install -r requirements-gpu.txt` ; diagnostic :
  `python3 -m tradehelper.compute`.
- **Conseils** : score combiné technique (65 %) + fondamental (35 % pour les actions :
  PER vs secteur, PEG, marges, croissance, dette, avis analystes, objectif de cours),
  traduit en avis lisible (Achat fort → Vente) avec raisons, niveau de risque,
  stop suggéré (2×ATR) et horizon conseillé.
- **Sélection d'actif** : liste déroulante d'actifs connus par source (43 actions US/Europe
  + 15 ETF, 14 paires crypto, 12 paires forex — voir `tradehelper/data/universe.py`),
  **saisie libre possible** pour n'importe quel symbole. Changer de source met à jour
  la liste, l'actif par défaut et les intervalles disponibles.
- **Un seul instrument pour toute l'app** : la barre du haut (source / actif / période /
  intervalle) est **commune aux 3 onglets** — le backtest porte toujours sur l'actif
  sélectionné, quel que soit l'onglet d'où il est lancé.
- **Interface graphique** (Tkinter + matplotlib) avec 4 onglets :
  1. **Analyse & conseils** — graphique en chandeliers avec moyennes mobiles, Bollinger,
     RSI, MACD, volume, marqueurs d'achat/vente de la stratégie choisie + panneau d'avis.
     **Survol du graphique** : réticule + infobulle (date, OHLC, volume, RSI de la barre).
  2. **Backtest** — choix de la stratégie et de ses paramètres, capital, commission,
     slippage, sizing ; affiche la courbe d'equity, le drawdown, les métriques et la
     liste des trades (triable).
  3. **Screener multi-stratégies** — pour chaque symbole de la liste, **toutes les
     stratégies sont backtestées** ; la meilleure est retenue (Sharpe, repli sur le
     rendement si moins de 2 trades) et une **recommandation** est produite : position
     (ACHAT / ATTENTE / SHORT selon le dernier signal), horizon (durée moyenne des
     trades, ex. « ≈ 14 j »), prix d'entrée, stop-loss (2×ATR) et objectif (R:R 2,5).
     Colonne **type** (Action / ETF / Crypto / Forex). **Double-clic** sur une ligne :
     l'actif ET la stratégie recommandée sont chargés dans le contexte global,
     l'onglet Analyse s'ouvre et s'analyse. La **liste par défaut** mélange actions et
     ETF (~1/3 d'ETF : SPY, QQQ, VOO…) pour ne pas manquer les ETF.
     Une **watchlist persistante** (SQLite) permet de suivre ses actifs favoris.
  4. **Portefeuille multi-actifs** — backtest d'un panier (parts égales, matrice de
     corrélation, frais par exécution).
- **UI/UX** : thème **clair / sombre** (persisté, Ctrl+D), zoom du texte (Ctrl++ / Ctrl+-),
  **menubar** (Fichier / Onglets / Affichage / Aide), **raccourcis clavier** (Ctrl+1..4
  onglets, Ctrl+R analyse, Ctrl+B backtest, Ctrl+S screener, F1 aide), infobulles sur les
  boutons, barre de statut avec le backend de calcul actif (CPU / GPU CUDA) et une
  **barre de progression** pendant les calculs (graduée pour le screener : x/N actifs ;
  animée sinon — analyse, backtest, walk-forward, grid search, Monte Carlo, portefeuille).
- **Exports** : graphique en **PNG** (Ctrl+E), résultats du screener et trades en **CSV**,
  rapport affiché en **TXT** ; **historique des analyses** (screener / backtest /
  portefeuille) consultable et effaçable (Ctrl+H), stocké en SQLite.

## Installation

```bash
pip install --user -r requirements.txt   # pandas, numpy, matplotlib, yfinance, requests, pillow
```

## Lancement

```bash
python3 run.py
```

## Utilisation en ligne de commande (exemple)

```python
from tradehelper.data import load_data
from tradehelper.indicators import add_all
from tradehelper.strategies import get_strategy
from tradehelper.backtest import run_backtest, periods_per_year
from tradehelper.advice import advise

df = add_all(load_data("stock", "AAPL", period="1y", interval="1d"))
print(advise(df, "AAPL", source="stock")["label"])

sig = get_strategy("ma_cross", fast=12, slow=26).generate(df)
res = run_backtest(df, sig, initial_capital=10_000, commission=0.001, slippage=0.0005,
                   periods_per_year=periods_per_year("1d", "stock"))
print(res.metrics)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Structure

```
tradeHelper/
├── run.py                  # point d'entrée de la GUI
├── requirements.txt
├── tradehelper/
│   ├── data/sources.py     # Yahoo, Binance, BCE + cache CSV
│   ├── indicators.py       # indicateurs techniques
│   ├── strategies/         # 4 stratégies + registre
│   ├── backtest/           # moteur + métriques
│   ├── analysis/           # score technique + fondamental
│   ├── advice.py           # avis combiné
│   ├── screener.py         # screener multi-stratégies + recommandation (position/horizon/niveaux)
│   └── gui/                # Tkinter : analyse / backtest / screener + graphiques
└── tests/                  # tests unitaires (données synthétiques)
```

## Avertissement

Cet outil est fourni à titre **éducatif et d'aide à la décision** uniquement. Il ne constitue
pas un conseil en investissement. Les backtests passés ne préjugent pas des performances
futures ; le trading comporte un risque de perte en capital.
