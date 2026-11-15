# Guide utilisateur — tradeHelper

> Avertissement : tradeHelper est un **outil éducatif**, pas un conseil en investissement.
> Les données sont ouvertes (Yahoo Finance, Binance, BCE) et peuvent être retardées ou erronées.

## Lancement

```bash
pip install --user -r requirements.txt
python3 run.py            # nécessite un affichage (X11/Wayland)
```

## La barre d'instrument (en haut, commune aux 5 onglets)

| Champ | Rôle |
|---|---|
| Source | `stock` (actions/ETF), `crypto`, `forex` |
| Actif | liste déroulante (univers) ou **saisie libre** |
| Période | `1mo` … `5y`, `max` (historique complet quand la source le permet) |
| Intervalle | selon la source (ex. `1d`, `1h`, `15m` ; forex = `1d` uniquement) |
| Calcul | `auto` / `cpu` / `cuda` — en `cuda`, indicateurs GPU (cupy) + ML XGBoost CUDA |

## Onglet 1 — Analyse & conseils

`Charger & analyser l'actif` (Ctrl+R) : graphique en chandeliers + SMA/EMA, Bollinger,
RSI, MACD, volume, marqueurs d'achat/vente de la stratégie choisie, et panneau d'avis
(score technique 65 % + fondamental 35 % pour les actions : PER vs secteur, PEG, marges,
croissance, dette, avis analystes, **surprise de BPA**, **sentiment news (RSS)**,
calendrier des résultats).

- **Survol du graphique** : réticule + infobulle (date, OHLC, volume, RSI).
- Le stop suggéré est à 2×ATR sous le dernier cours.

## Onglet 2 — Backtest

Choisissez la stratégie et ses paramètres, le capital, le slippage, le sizing
(`all_in` ou `risk` = % du capital risqué par trade), les frais (préréglage
**Trade Republic FR par défaut** : 1 €/ordre actions/ETF, 1 % min 1 € max 50 € crypto),
le vol targeting et le stop de drawdown global.

- **Backtest simple** (Ctrl+B) : courbe d'equity + drawdown, métriques (Sharpe, Sortino,
  Calmar, win rate, profit factor, expectancy…) et liste des trades (triable par colonne).
- **Walk-forward** : blocs rolling, grid search sur le train, equity out-of-sample chaînée
  (mesure d'overfitting vs in-sample).
- **Grid search** : score = Sharpe pénalisé par l'instabilité vs configurations voisines.
- **Monte Carlo** : bootstrap des rendements des trades (proba de profit, percentiles).

Le moteur est **sans lookahead** : un signal de la barre *i* est exécuté à l'open de *i+1*.

## Onglet 3 — Screener multi-stratégies

`Scanner` (Ctrl+S) backteste **toutes les stratégies** sur chaque symbole de la liste :
meilleure stratégie retenue (Sharpe), recommandation avec **position** (ACHAT/ATTENTE/SHORT),
**horizon**, entrée, stop (2×ATR) et objectif (R:R 2,5). Colonne **type** (Action/ETF/Crypto/Forex),
colonnes **triables** (clic sur l'en-tête), **double-clic** = ouvre l'actif + la stratégie
recommandée dans l'onglet Analyse. Le bandeau « Conseil du jour » indique quoi acheter
aujourd'hui. La **liste par défaut** mélange actions et ETF.

- **Watchlist** (persistante, SQLite) : `+ actif courant`, `- retirer`, `Charger` (double-clic aussi).
- Barre de progression pendant le scan (x/N actifs).

## Onglet 4 — Portefeuille multi-actifs

Backtest d'un panier (parts égales entre actifs en position, frais par exécution,
matrice de corrélation, limite de corrélation optionnelle).

## Onglet 5 — Paper trading & journal (Ctrl+5)

- **Portefeuille fictif persistant** (SQLite) : capital initial, nombre max de positions,
  frais réels (modèle de frais choisi, TR FR par défaut). Trésorerie, equity, P&L
  latent/réalisé et win rate affichés en permanence.
- **Sync avec le screener** (« Sync paper avec ce scan ») : ouvre une position sur chaque
  ligne en position ACHAT, clôture celles dont le signal n'est plus ACHAT ou dont le stop /
  l'objectif est touché.
- **Ouvrir l'actif courant** : ouvre une position paper depuis l'avis de l'onglet Analyse
  (après Ctrl+R). Clôture manuelle par sélection, reset complet.
- **Journal de trading** : notes / entrées / sorties / idées avec symbole et capture
  d'écran, persisté en SQLite.
- **Broker** : « paper » par défaut (aucun ordre réel). Un broker IBKR réel reste
  verrouillé sans `TRADEHELPER_ALLOW_LIVE_TRADING=1` + confirmation + plafond de notionnel.

## Exports & historique

Menu **Fichier** : PNG du graphique actif (Ctrl+E), CSV du screener (Ctrl+Shift+S),
CSV des trades, TXT du rapport affiché, **historique des analyses** (Ctrl+H, stocké en SQLite).

## Affichage & accessibilité

- **Thème sombre** (Ctrl+D) et zoom du texte (Ctrl++ / Ctrl+-), persistés.
- **Raccourcis** : Ctrl+1..5 (onglets), Ctrl+R/B/S (actions), Ctrl+H (historique),
  F1 (aide), Ctrl+Q (quitter). Infobulles au survol des boutons.
- **Langue** : Français (défaut) / English — menu Affichage, persisté
  (`TRADEHELPER_LANG=fr|en` au lancement).

## Mode GPU (optionnel)

Sur une machine avec carte NVIDIA (ex. RTX 5060 Ti 16 Go ; les RTX 50 nécessitent
CUDA ≥ 12.8) :

```bash
pip install --user -r requirements-gpu.txt   # cupy + xgboost[cu] compatibles CUDA
python3 -m tradehelper.compute                # diagnostic
```

Puis « Calcul : cuda » dans la barre du haut (ou `TRADEHELPER_BACKEND=cuda`).
Sans GPU, repli CPU automatique. En mode CPU, le screener parallélise les actifs
sur plusieurs processus.

## Configuration

Fichier JSON pointé par `TRADEHELPER_CONFIG`, ou variables d'environnement :
`TRADEHELPER_DB_PATH`, `ALPHAVANTAGE_API_KEY`, `TRADEHELPER_ADJUST_PRICES`,
`TRADEHELPER_BACKEND`, `TRADEHELPER_LANG`. Voir `tradehelper/config.py` pour toutes les clés.

Stockage : SQLite `~/.local/share/tradehelper/market.db` (barres + cache Parquet
`parquet/` à côté + watchlist + historique + préférences UI), mise à jour incrémentale.
