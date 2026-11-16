# Cahier des charges — tradeHelper (SPEC)

| Champ | Valeur |
|---|---|
| Projet | tradeHelper — outil d'aide au trading (swing / day) |
| Version du document | 1.1.0 |
| Date | 2026-11-15 |
| Statut | Vivant (mis à jour à chaque évolution de périmètre) |
| Auteur | lasout |
| Dépôt | https://github.com/Lasout/Mimir (public, licence MIT) |

Conventions : chaque exigence a un **ID stable** (`DOM-xx`), une **priorité** (P0…P5 =
phases de la [ROADMAP](ROADMAP.md)) et un **statut** (`fait` / `en cours` / `à faire`).
On ne renumérote jamais un ID : un changement = nouvelle entrée + historique en bas.

---

## 1. Contexte et objectifs

Outil d'aide à la décision pour le **swing / day trading**, sur **données ouvertes**
(gratuites, sans clé API obligatoire), combinant **analyse technique** et
**analyse fondamentale**, avec backtesting, screener multi-stratégies et paper trading.

Objectif central (hérité du cahier des charges initial) : mesurer **honnêtement** la
performance du moteur de conseils **avant tout capital réel**, en garantissant que le
backtest et les conseils quotidiens ne contiennent **aucun biais statistique**. Le paper
trading ne sert à rien s'il mesure un moteur biaisé : la fiabilité de la mesure est donc
la partie prioritaire.

> Avertissement permanent : outil **éducatif**, pas un conseil en investissement.

## 2. Périmètre

### 2.1 Inclus
- Données OHLCV persistantes (SQLite + cache Parquet optionnel) : actions/ETF (Yahoo,
  Alpha Vantage en repli), crypto (Binance paginé), forex (taux BCE/Frankfurter).
- Indicateurs techniques, 6 stratégies, moteur de backtest sans lookahead, walk-forward,
  grid search, Monte Carlo, portefeuille multi-actifs.
- Screener multi-stratégies avec recommandation (position, horizon, entrée/stop/objectif)
  et conseil du jour ; avis combiné technique/fondamental ; calendrier (earnings, BCE/FOMC).
- GUI Tkinter (5 onglets), i18n fr/en, thèmes, exports, watchlist, historique des runs.
- Paper trading + journal + broker avec garde-fous.
- Qualité : mypy strict sur le cœur, tests hors-ligne + tests réseau marqués, CI/CD,
  releases PyInstaller, scan de secrets.

### 2.2 Exclus (hors périmètre)
- Alertes temps réel (Telegram/Discord/email) — refusées pour l'instant.
- Exécution d'ordres réels sans validation explicite (voir GOU-xx et GNG-xx).
- Données tick/level-2, websocket temps réel.

## 3. Exigences fonctionnelles

### 3.1 Données — DAT (P1, fait)
- DAT-01 Historique long persistant (store SQLite + Parquet), prix ajustés par défaut. **fait**
- DAT-02 Chaîne de fournisseurs avec repli, TTL de fraîcheur, plafond de barres/téléchargement. **fait**
- DAT-03 Univers d'actifs connu par source + saisie libre ; ETF distingués des actions. **fait**

### 3.2 Backtest & stratégies — BT (P2, fait)
- BT-01 Moteur sans lookahead : signal barre J exécuté au open de J+1. **fait** (test `test_no_lookahead_entry_next_bar`)
- BT-02 Frais (modèles de frais, TR FR par défaut), slippage, stop/TP intrabarre, sizing, vol targeting, stop de DD. **fait**
- BT-03 Walk-forward, grid search anti-overfitting, Monte Carlo, portefeuille multi-actifs. **fait**
- BT-04 6 stratégies prêtes à l'emploi + registre extensible. **fait**

### 3.3 Screener & conseils — SCR (P2/P4, fait)
- SCR-01 Toutes les stratégies backtestées par actif ; meilleure retenue (Sharpe, repli rendement). **fait**
- SCR-02 Recommandation position + horizon + entrée/stop/objectif par actif ; conseil « quoi acheter aujourd'hui ». **fait**
- SCR-03 Progression verbeuse par phase (chargement / avis / backtest k/N + ETA). **fait**
- SCR-04 Pas d'appels réseau fondamentaux/earnings pour les ETF. **fait**
- SCR-05 Avis 65 % technique / 35 % fondamental (PER vs secteur, surprise BPA, sentiment news, calendrier). **fait**

### 3.4 Interface — GUI (P4, fait)
- GUI-01 5 onglets (Analyse / Backtest / Screener / Portefeuille / Paper & journal), raccourcis, menubar, i18n fr/en, thèmes, exports, watchlist, historique. **fait**

### 3.5 Paper trading conforme — PAP (P6, **fait** : `tradehelper/paper_engine.py` + table `paper_trades` + tests `test_paper_engine.py` + section GUI « Moteur conforme »)

**Principe** : un moteur d'exécution simulée qui traite chaque conseil du jour comme un
ordre réel : remplissage au prochain prix disponible après le signal, frais et slippage
appliqués, résultat enregistré en **multiple de R** (R = gain/perte ÷ risque initial au stop).

- PAP-01 **Remplissage au open de J+1** (slippage inclus), jamais au prix de clôture de J
  ni au prix du signal. *Le backtest applique déjà cette règle ; le paper doit être refondu dessus.*
- PAP-02 Signal enregistré avec les **données de J seulement** (entrée planifiée, stop, cible, risque %).
- PAP-03 Suivi quotidien : stop et cible vérifiés sur les données de la journée ; si les deux
  sont touchés le même jour, **stop prioritaire** (convention conservatrice).
- PAP-04 Clôture → calcul du **R-multiple net de frais et slippage**, écrit dans le journal.
- PAP-05 Exécution **idempotente** (rejouer le même jour = aucun doublon ; clé
  `trade_id` = hash(strategy_version, asset, signal_date)) et **horodatée à l'horloge réelle**.
- PAP-06 **Modèle de coûts non négociable** : frais aller-retour ≥ 0,1 % du notionnel
  (configurable avec ce minimum comme garde-fou) ; slippage fixe (ex. 0,05 %/côté) ou basé
  sur la volatilité récente, appliqué **contre** la position à l'entrée et à la sortie.
  Aucun trade/conseil/métrique calculé sans coûts ; un bouton « métriques brutes » peut
  exister mais n'est **jamais** le chiffre de référence.
- PAP-07 **Schéma du journal** (une ligne = un trade, du signal à la sortie) :
  `trade_id`, `strategy_version`, `signal_date`, `asset`, `direction` (long|short),
  `planned_entry`, `stop`, `target`, `risk_pct`, `fill_price`, `fill_date`, `exit_date`,
  `exit_price`, `exit_reason` (stop|target|règle de sortie|fin de période), `fees`,
  `r_planned`, `r_realized` (null si ouvert).
- PAP-08 **Tableau de bord** (recalculé uniquement sur trades clôturés) :
  - espérance par trade en R (cible opérationnelle **+0,15 R à +0,30 R**) ;
  - **taux de réussite et ratio gain/perte moyen ensemble** (jamais l'un sans l'autre) ;
  - **drawdown maximal en R et en %** du capital simulé ;
  - benchmark **buy-and-hold / DCA des mêmes actifs sur la même fenêtre** ;
  - nombre de trades clôturés vs **win rate d'équilibre** = 1/(1+payoff).

### 3.6 Audit anti-biais — AUD (P6, en cours : AUD-01 et AUD-03 faits ; chaque point = **test automatisé** qui doit passer avant de croire toute statistique)

- AUD-01 **Look-ahead (priorité absolue)** : tout signal du jour J est calculé avec des
  données ≤ J. Test de non-régression : 20 jours historiques aléatoires (seed fixe),
  tronquer à J, recalculer le conseil, vérifier l'identité avec le conseil « d'époque ».
  Règles pandas : `shift()` systématique, aucun resample/merge sur l'ensemble complet avant
  le split train/test, normalisation/indicateurs recalculés sur la fenêtre passée seulement,
  chasse aux indicateurs « repaint » (ZigZag, fractales non confirmées, Supertrend recalculé).
  **Fill du jour même interdit** (backtest invalide par construction s'il remplit au prix du signal).
  **Statut : fait** — `tests/test_antibias.py` (6 tests : invariance par troncature à J des
  indicateurs, des signaux des 6 stratégies, de l'avis, du préfixe d'equity ; fill = open
  de J+1). A détecté et corrigé un vrai leak dans `ml_signal` (prédiction sautée quand le
  label était NaN, entraînement sinon sur le label du futur) : entraînement sur labels `< i`,
  prédiction systématique à `i`.
- AUD-02 **Survivorship bias** : l'univers éligible à J est celui connu à J (crypto : inclure
  disparus/illiquides). Test : aucune info postérieure (délisting, cap/volume actuels) dans
  le filtre historique.
- AUD-03 **Cherry-picking** : toutes les métriques incluent chaque signal émis. Test :
  `len(journal) == signaux émis par strategy_version`.
  **Statut : fait (niveau paper)** — `apply_screener` retourne `n_signals` (nb de lignes de
  scan traitées) et chaque ligne non actionnée est explicitement dans `skipped` ; test
  `test_every_signal_row_is_counted`.
- AUD-04 **Overfitting des paramètres** : version de stratégie = jeu de paramètres gelé ;
  toute modification = nouvelle version (compteur d'échantillon remis à zéro). Backtest :
  split temporel train/test, walk-forward (optimiser sur fenêtre glissante, valider sur la
  suivante). Vigilance sur le nombre de paramètres (un bon backtest avec beaucoup de
  paramètres est probablement du bruit ajusté).
- AUD-05 **Régime de marché** : taguer le régime de la période testée (haussier/plat/baissier
  via tendance d'un indice de référence) sur chaque métrique ; aucune conclusion préliminaire
  hors échantillon couvrant **≥ 2 régimes**.
- AUD-06 **Significativité statistique** : **≥ 100 trades clôturés** par version avant toute
  conclusion. Afficher l'intervalle de confiance de l'espérance (± 2·σ_R/√n) ; si l'IC
  contient 0, afficher « on ne sait rien ».

### 3.7 Gouvernance du test — GOU (P6, **fait** : `PaperEngine.start_test/active_test/add_idea/cancel_signal/check_review`)

- GOU-01 La stratégie testée en paper est **figée et versionnée** (`strategy_version` =
  hash des params) ; `record_signal` refuse toute autre version que celle du test en cours ;
  carnet d'idées séparé (`add_idea`, journal kind='idée'). **fait**
- GOU-02 Capital simulé, risque par trade (ex. 1 %) et modèle de coûts **fixés en début de
  test** (`start_test` écrit la config dans `meta`, réinitialise capital + échantillon ;
  `dashboard` filtré par `test_id`). **fait**
- GOU-03 **Aucun décrochage manuel** : `cancel_signal()` lève une erreur par design. **fait**
- GOU-04 Revue à chaque palier de 100 trades clôturés (`check_review` loggue `paper_review`
  dans l'historique des runs). **fait**

### 3.8 Qualité & distribution — QUA (P0/P5, fait)
- QUA-01 mypy `--strict` sur le cœur (backtest, strategies, screener) ; lint ruff ; pre-commit. **fait**
- QUA-02 Tests hors-ligne (unittest/pytest) + couverture > 80 % sur le cœur (93 %) ; tests
  réseau marqués (`TRADEHELPER_NETWORK_TESTS=1`). **fait**
- QUA-03 CI/CD GitHub Actions (lint, tests multi-Python 3.11–3.15, mypy, gitleaks), releases
  `v*` PyInstaller multi-OS, CHANGELOG. **fait**
- QUA-04 Secrets : `.env` non commité + variables d'environnement + scan de secrets. **fait**

## 4. Exigences non-fonctionnelles

- NF-01 **Pas de lookahead** dans le backtest ni dans le moteur de conseils (voir AUD-01).
- NF-02 UI en français par défaut (i18n fr/en), sans emoji ; avertissement « outil éducatif,
  pas un conseil » maintenu.
- NF-03 GUI thread-safe (jamais d'appel Tk depuis un worker) ; tests 100 % hors-ligne et
  sans display (GUI testée par scripts de fumée manuels uniquement).
- NF-04 Calcul GPU optionnel (`auto`/`cpu`/`cuda`), repli CPU automatique.
- NF-05 Aucun ordre réel sans validation explicite (voir GNG-xx).

## 5. Critères de décision go/no-go pour le capital réel — GNG (P6, à faire ; cadsup §5)

Tous les critères doivent être remplis **simultanément** ; un seul manquant = on continue
le paper trading.

| ID | Critère | Seuil de passage |
|---|---|---|
| GNG-01 | Trades clôturés | ≥ 100 sur la version en cours |
| GNG-02 | Espérance nette | > +0,15 R par trade, IC excluant 0 |
| GNG-03 | Payoff | ratio gain/perte ≥ 1,5 **ou** win rate ≥ 45 % (l'un des deux, avec espérance positive) |
| GNG-04 | Drawdown simulé | max ≤ 20–25 % du capital simulé |
| GNG-05 | Benchmark | performance nette > buy-and-hold des mêmes actifs, même période |
| GNG-06 | Régimes | échantillon couvrant ≥ 2 régimes de marché |

Le déblocage du broker réel (IBKR) reste verrouillé tant que la grille GNG n'est pas verte
**et** que `TRADEHELPER_ALLOW_LIVE_TRADING=1` + confirmation manuelle + plafond de
notionnel ne sont pas réunis.

## 6. Références

- [ROADMAP.md](ROADMAP.md) — plan de réalisation (P0…P6), cases cochées = fait.
- [AGENTS.md](AGENTS.md) — contexte pour agents (architecture, règles, commandes).
- [README.md](README.md) — présentation, installation, lancement.
- [docs/guide-utilisateur.md](docs/guide-utilisateur.md) — guide utilisateur.
- [CHANGELOG.md](CHANGELOG.md) — historique des versions (Keep a Changelog).

## Historique des versions de ce document

| Version | Date | Changement |
|---|---|---|
| 1.0.0 | 2026-11-15 | Cahier des charges initial (`cadsup.md`) : paper trading conforme + audit anti-biais + go/no-go. |
| 1.1.0 | 2026-11-15 | Restructuration en SPEC global (IDs stables, statuts, priorités) ; `cadsup.md` intégré en §3.5–§3.8 et supprimé. |
| 1.1.1 | 2026-11-15 | AUD-01 (look-ahead) et AUD-03 (cherry-picking) faits : `tests/test_antibias.py` ; correction du leak look-ahead dans `ml_signal`. |
| 1.2.0 | 2026-11-16 | P6.1 fait : moteur paper conforme (PAP-01…08) — `paper_engine.py`, table `paper_trades`, dashboard + benchmark B&H, GUI. |
| 1.3.0 | 2026-11-16 | P6.2 fait : gouvernance (GOU-01…04) — `start_test` (config figée, test_id), version verrouillée, carnet d'idées, `cancel_signal` interdit, revue/100 trades. |
