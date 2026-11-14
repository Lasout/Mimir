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

## P1 — données : historique plus long et persistant

- [x] **Historique long** : période `max` ; pagination Binance en boucle (1000 klines/appel,
      plafond `max_bars_per_fetch`) ; Yahoo `period="max"` pour actions daily/hebdo/mensuel
- [x] **Base SQLite** à la place du cache CSV : `~/.local/share/tradehelper/market.db`,
      upsert incrémental (tête sondée 1×/24 h, queue selon TTL), requêtes par symbole/intervalle
- [x] **Fournisseurs configurables** avec fallback : `config.providers` (défaut
      stock: yahoo→alphavantage, crypto: binance→yahoo, forex: frankfurter→yahoo) ;
      Alpha Vantage si `ALPHAVANTAGE_API_KEY` ; config via `TRADEHELPER_CONFIG` (JSON) + env
- [x] **Calendrier des annonces** : date des résultats (yfinance `calendar`), cachée 24 h
      dans le store, affichée dans l'avis avec alerte si < 14 j
- [ ] Ajustement des prix (dividendes/splits) : flag `adjust_prices` dans la config
      (Yahoo `auto_adjust`), à activer par défaut après vérification de l'impact backtest
- [ ] Calendrier BCE/Fed (pas de source gratuite fiable identifiée — scraping à étudier)
- [ ] Stooq : inutilisable (challenge JS anti-bot) — documenté, ne pas réessayer sans navigateur

## P2 — backtest & stratégies (niveau pro)

- [ ] **Walk-forward analysis** (fenêtres rolling train/test) + rapport d'overfitting
- [ ] Optimisation de paramètres (grid/random search) avec pénalité de complexité
- [ ] Monte Carlo (permutation des trades / bootstrap des rendements)
- [ ] Backtest multi-actifs / portefeuille (corrélation, exposition nette, drawdown global)
- [ ] Frais réalistes : funding crypto, borrow pour le short, slippage dépendant du volume
- [ ] Stratégies : support short natif, mean-reversion multi-actifs, détection de régime
      (tendance/range via ADX + HMM optionnel)
- [ ] Signaux ML : features techniques + `scikit-learn`, validation croisée temporelle
- [ ] Risk management avancé : vol targeting, sizing de Kelly fractionnaire, stop de
      drawdown global, limites de corrélation

## P3 — alertes, paper trading, exécution

- [ ] Alertes sur signaux (Telegram/Discord via webhook, email) avec niveaux entrée/stop/objectif
- [ ] **Paper trading** : portefeuille fictif persistant, suivi des recommandations du screener
- [ ] Connexion broker (ex. IBKR API) — **avec garde-fous** (montants max, confirmation
      manuelle, mode paper par défaut). Ne jamais exécuter d'ordres réels sans validation explicite
- [ ] Journal de trading (notes, screenshots, raisons d'entrée/sortie)

## P4 — analyse & UI/UX

- [ ] Fondamental avancé : scoring sectoriel relatif, surprise de BPA, sentiment news
      (RSS / API gratuite), données alternatives
- [ ] Watchlist persistante, historique des backtests et des avis (SQLite)
- [ ] Export des rapports (CSV, PDF) et des graphiques (PNG)
- [ ] Graphiques interactifs (mplcursors : OHLC au survol), thème sombre, onglet portefeuille
- [ ] i18n (fr/en), accessibilité clavier, raccourcis
- [ ] Performance : screener parallélisé (process pool), chargement lazy, cache Parquet
- [ ] Documentation utilisateur (MkDocs/Sphinx) + tutoriel « premier backtest »

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
