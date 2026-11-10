# ADR 0055 — File équitable et quotas par utilisateur

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0028 (runs parallèles), 0050 (budget de cœurs), 0052 (mode serveur)

## Contexte
Sur un serveur partagé, les runs prenaient leur tour du plus ancien au plus récent : un lot de cinquante runs lancé
par une personne faisait attendre tout le monde. Mémoire et cœurs se divisaient entre les runs en cours, sans égard
à qui les lance : celui qui en lance le plus prenait la plus grande part de la machine.

## Décision
1. **File équitable** : parmi les runs en attente, passe d'abord celui dont le propriétaire a le moins de runs en
   cours, puis le plus ancien. La limite de runs simultanés par utilisateur (`ETL_MAX_RUNNING_PER_USER`, 2 en mode
   serveur) reste.
2. **Quotas** (`ETL_USER_MEMORY_MB`, `ETL_USER_CPU_BUDGET`, facultatifs) : la mémoire et les cœurs que tous les runs
   d'un utilisateur se partagent. Un run reçoit sa part de la machine (budget divisé par les runs en cours), au plus
   sa part du quota de son propriétaire (quota divisé par ses runs en cours).
3. Les trois réglages se règlent dans *Paramètres → Calcul* (administrateurs, mode serveur) et s'appliquent au
   redémarrage du moteur. Sur le poste de bureau : un seul utilisateur, aucun quota.

## Conséquences
- Un utilisateur seul sur le serveur garde toute la machine si aucun quota n'est fixé.
- Un quota trop petit ralentit ses runs (débordement sur disque) sans les faire échouer.
