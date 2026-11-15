# Outil de screening : module paper trading et audit anti-biais du moteur de conseils

## 1. Objectif

Mesurer honnêtement la performance du conseil du jour **avant tout capital réel**, en garantissant que le backtest et les conseils quotidiens ne contiennent aucun biais statistique. Le paper trading ne sert à rien s'il mesure un moteur biaisé : la fiabilité de la mesure est donc la partie 1 du chantier.

---

## 2. Module paper trading

### 2.1 Principe

Un moteur d'exécution simulée qui traite chaque conseil du jour comme un ordre réel : remplissage au prochain prix disponible après le signal, frais et slippage appliqués, résultat enregistré en multiple de R (R = gain ou perte divisé par le risque initial au stop).

### 2.2 Cycle quotidien (exécution idempotente)

1. Après la clôture de la journée J, le screener produit le conseil (ou aucun signal).
2. Le moteur paper trading enregistre le signal **avec les données de J seulement** (prix d'entrée planifié, stop, cible, risque %).
3. Le remplissage simulé se fait **au prix d'ouverture de J+1**, jamais au prix de clôture de J. Interdiction de remplir au prix du signal.
4. Position ouverte → suivi quotidien : stop et cible vérifiés sur les données de la journée ; si stop et cible touchés le même jour, convention conservatrice : **stop prioritaire**.
5. À la clôture de la position : calcul du R-multiple net de frais et slippage, écriture dans le journal.
6. Chaque exécution doit être idempotente (relancer le même jour ne crée pas de doublon) et horodatée avec l'horloge réelle (pas d'horodatage manuel).

### 2.3 Modèle de coûts (non négociable)

- Frais aller-retour : minimum 0,1 % du notionnel (adapter selon l'exchange visé).
- Slippage : fixe (ex. 0,05 % par côté) ou basé sur la volatilité récente ; appliqué **contre** la position à l'entrée et à la sortie.
- Aucun trade, aucun conseil, aucune métrique n'est calculé sans coûts appliqués. Un bouton « métriques brutes » peut exister, mais jamais comme chiffre de référence.

### 2.4 Schéma du journal (une ligne = un trade, du signal à la sortie)

```json
{
  "trade_id": "string, unique",
  "strategy_version": "string, ex. 1.2.0",
  "signal_date": "YYYY-MM-DD, jour de génération du conseil",
  "asset": "string",
  "direction": "long | short",
  "planned_entry": "float",
  "stop": "float",
  "target": "float",
  "risk_pct": "float, % du capital engagé",
  "fill_price": "float, ouverture J+1 avec slippage",
  "fill_date": "YYYY-MM-DD",
  "exit_date": "YYYY-MM-DD | null",
  "exit_price": "float | null",
  "exit_reason": "stop | target | règle de sortie | fin de période",
  "fees": "float, cumulé entrée+sortie",
  "r_planned": "float, (cible-entrée)/(entrée-stop)",
  "r_realized": "float | null, net de frais et slippage"
}
```

### 2.5 Métriques du tableau de bord

Trois chiffres de pilotage, recalculés uniquement sur trades **clôturés** :

- **Espérance par trade** (R moyens) : cible opérationnelle entre +0,15 R et +0,30 R.
- **Taux de réussite** et **ratio gain/perte moyen** (jamais l'un sans l'autre : un win rate de 60 % avec un payoff &lt; 1 est perdant).
- **Drawdown maximal en R** et en % du capital simulé.

Plus deux comparaisons de contexte, mêmes actifs, même période :

- Buy-and-hold (ou DCA) des mêmes actifs sur la même fenêtre.
- Nombre de trades clôturés et marge par rapport au seuil d'équilibre (win rate de breakeven = 1 / (1 + payoff)).

---

## 3. Audit anti-biais du backtest et du moteur de conseils

Chaque point ci-dessous se traduit en **test automatisé** qui doit passer avant de croire toute statistique produite par l'outil.

### 3.1 Look-ahead bias (biais d'anticipation) — priorité absolue

- **Règle** : tout signal du jour J est calculé exclusivement avec des données de clôture strictement antérieures ou égales à J (et le remplissage simulé se fait en J+1, section 2.2).
- **Test de non-régression** : pour 20 jours historiques tirés au hasard, tronquer les données au jour J, recalculer le conseil, et vérifier qu'il est identique à celui produit à l'époque (ou à celui du backtest). Tout écart = fuite de données futures.
- **Points de contrôle typiques** en Python/pandas : `shift()` systématique avant toute comparaison ligne à ligne ; aucun `resample` ou `merge` sur l'ensemble complet avant la séparation train/test ; normalisation et indicateurs recalculés uniquement sur la fenêtre passée ; attention aux indicateurs « repaint » (ex. ZigZag, fractales non confirmées, SUPERTENDANCE recalculée).
- **Fill du jour même interdit** : si le backtest remplit au prix du signal, il est invalide par construction.

### 3.2 Survivorship bias

- La liste des actifs éligibles au jour J doit être **celle connue au jour J**, pas la liste d'aujourd'hui. En crypto : inclure les actifs depuis disparus ou illiquides dans l'univers de screening historique.
- Test : vérifier qu'aucune information postérieure (délisting, capitalisation actuelle, volume actuel) n'entre dans le filtre de sélection historique.

### 3.3 Biais de sélection des résultats (cherry-picking)

- Toutes les métriques globales incluent **chaque** signal émis, y compris ceux non suivis manuellement. Aucune exclusion rétrospective de « mauvais trades ».
- Test : le compte de lignes du journal doit être égal au compte de signaux émis par version de stratégie.

### 3.4 Overfitting des paramètres

- **Version figée** : une version de stratégie = un jeu de paramètres gelé. Toute modification de paramètre crée une nouvelle version (`strategy_version` du journal) et **remet le compteur d'échantillon à zéro**. Jamais d'ajustement de paramètres au fil des résultats, sinon le paper trading ne mesure plus rien.
- Sur le backtest : séparer train/test dans le temps (ex. optimisation sur 2023-2024, validation sur 2025+), idéalement walk-forward (optimiser sur fenêtre glissante, valider sur la fenêtre suivante, jamais l'inverse).
- Vigilance sur le nombre de paramètres : plus l'outil en a, plus un bon backtest est probablement du bruit ajusté.

### 3.5 Biais de régime de marché

- Signaler le régime de la période testée (haussier / plat / baissier, ex. via la tendance d'un indice de référence) sur chaque métrique. Un conseil d'achat qui gagne uniquement quand le marché monte n'a pas d'edge, il a un bêta.
- Consigne : tirer toute conclusion préliminaire uniquement sur un échantillon couvrant au moins deux régimes différents.

### 3.6 Significativité statistique

- Seuil minimal : **100 trades clôturés** par version avant toute conclusion. En dessous, le résultat est du bruit ; même un vrai edge laisse \~12 % de trajectoires négatives sur 100 trades.
- Afficher un intervalle de confiance simple sur l'espérance (ex. ± 2 × écart-type des R / √n) : si l'intervalle contient 0, on ne sait rien.

---

## 4. Règles de gouvernance du test

1. La stratégie testée en paper est **figée et versionnée** ; un carnet d'idées séparé recueille les améliorations souhaitées sans les appliquer à la version en cours.
2. Le capital simulé, le risque par trade (ex. 1 %) et le modèle de coûts sont fixés en début de test et ne changent plus.
3. Aucun décrochage manuel : pas d'annulation rétrospective d'un signal. Si l'outil émet un conseil absurde, c'est une information sur la stratégie, on le loggue, on le trade (virtuellement), on le note.
4. Revue à chaque palier de 100 trades clôturés.

## 5. Critères de décision (go / no-go pour capital réel)


| Critère         | Seuil de passage                                                                   |
| --------------- | ---------------------------------------------------------------------------------- |
| Trades clôturés | ≥ 100 sur la version en cours                                                      |
| Espérance nette | &gt; +0,15 R par trade, intervalle de confiance excluant 0                         |
| Payoff          | Ratio gain/perte ≥ 1,5 ou win rate ≥ 45 % (l'un des deux, avec espérance positive) |
| Drawdown simulé | Max ≤ 20-25 % du capital simulé                                                    |
| Benchmark       | Performance nette &gt; buy-and-hold des mêmes actifs sur la même période           |
| Régimes         | Échantillon couvrant au moins 2 régimes de marché                                  |


Tous les critères doivent être remplis simultanément. Un seul manquant = on continue le paper trading, on ne passe pas en réel.
