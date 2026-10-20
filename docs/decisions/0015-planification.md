# ADR 0015 — Planification

- **Statut** : accepté
- **Date** : 2026-10-07

## Contexte
Des flux doivent tourner chaque nuit ou chaque semaine sans que personne ouvre l'éditeur.

## Décision
- Table `schedules` (flux enregistré, expression cron, paramètres publiés, options, état). Pas de service
  supplémentaire : un **fil du worker** (`etl_worker.scheduler`) examine les échéances toutes les 10 s ; un verrou
  périodique en base (`try_acquire`) désigne un seul worker par tick, même avec N workers.
- Le planificateur **crée seulement des runs en attente** : concurrence, reprise, annulation, notification, purge
  sont celles des autres runs. Les définitions de composants sont épinglées dans le run (ADR 0014).
- Expression cron classique à cinq champs, implémentée sans dépendance (`etl_store.cron` : listes, intervalles, pas,
  noms, raccourcis `@daily`…, règle « jour du mois OU jour de semaine », heure d'été), fuseau `ETL_TIMEZONE`
  (Europe/Paris par défaut). L'interface affiche les prochaines échéances calculées par le serveur.
- Règles : une échéance **manquée** de plus d'une heure (workers arrêtés) est ignorée, pas rattrapée ; si le run
  précédent n'est pas terminé, l'échéance est **ignorée** (sauf « lancer même si… ») ; toute erreur (flux supprimé,
  paramètre invalide) est consignée dans `last_error` et la planification continue.
- « Lancer maintenant » crée un run avec les réglages de la planification, sans toucher à ses échéances.
- La ligne de commande reste utilisable depuis un cron externe : `etl run flux.json --param K=V`.

## Conséquences
- Sans worker vivant, rien n'est planifié (rien ne s'empile non plus : les échéances manquées sont ignorées).
- Précision : à 10 s près.
- Le propriétaire de la planification est celui du run (quotas par utilisateur).
