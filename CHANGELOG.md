# Changelog

Format [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), versions sémantiques
(MAJEUR.MINEUR.CORRECTIF). Les releases sont taguées `vX.Y.Z` sur GitHub (voir
`.github/workflows/release.yml` : paquets PyInstaller multi-OS).

Licence : [MIT](LICENSE) — réutilisation/fork libres avec citation de l'origine.

## [0.1.0] — 2026-11-15

Première version fonctionnelle complète (P0–P5 de la roadmap).

### Ajouté
- Données ouvertes persistantes (SQLite + cache Parquet optionnel) : Yahoo Finance,
  Binance (paginé), BCE/Frankfurter ; prix ajustés par défaut pour les actions.
- Indicateurs techniques (SMA/EMA, RSI Wilder, MACD, Bollinger, ATR, ADX, Donchian,
  OBV, VWAP) avec backend de calcul `auto`/`cpu`/`cuda` (GPU via cupy + XGBoost CUDA).
- 6 stratégies prêtes à l'emploi (`ma_cross`, `rsi_reversion`, `donchian_breakout`,
  `macd_momentum`, `adaptive_regime`, `ml_signal`) et moteur de backtest sans lookahead
  (frais, slippage, stop/TP intrabarre, sizing all-in/risk/Kelly, vol targeting,
  stop de drawdown global).
- Validation pro : walk-forward, grid search anti-overfitting, Monte Carlo des trades.
- Screener multi-stratégies : backteste toutes les stratégies par actif, recommande
  position + horizon + entrée/stop/objectif, conseil « quoi acheter aujourd'hui »,
  progression verbeuse par phase (chargement / avis / backtest k/N + ETA).
- Analyse & conseils : score 65 % technique + 35 % fondamental (PER vs secteur, PEG,
  marges, surprise de BPA, sentiment news RSS), calendrier (earnings, BCE/FOMC).
- GUI Tkinter 5 onglets (Analyse / Backtest / Screener / Portefeuille / Paper & journal),
  thème clair/sombre, i18n fr/en, raccourcis clavier, watchlist et historique des runs,
  exports PNG/CSV/TXT, graphiques interactifs (réticule + infobulle).
- Paper trading persistant suivant le screener + broker avec garde-fous (paper par
  défaut, IBKR verrouillé sans `TRADEHELPER_ALLOW_LIVE_TRADING=1` + confirmation +
  plafond) + journal de trading.
- Qualité : `mypy --strict` sur le cœur (backtest, stratégies, screener), 184+ tests
  hors-ligne, tests d'intégration réseau marqués (`TRADEHELPER_NETWORK_TESTS=1`),
  CI GitHub Actions (tests multi-version, mypy, scan de secrets gitleaks),
  releases taguées `v*` avec paquets PyInstaller, changelog, `.env` pour les clés.

### Sécurité
- Aucun ordre réel sans validation explicite ; `.env` non commité ; scan de secrets en CI.
