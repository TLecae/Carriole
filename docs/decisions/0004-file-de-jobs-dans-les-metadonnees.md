# ADR 0004 — File de jobs dans la base de métadonnées

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
Pas de broker. La table `runs` est la file : l'API insère un run `queued` ; un worker le
réserve atomiquement (`UPDATE … WHERE status='queued'` / `SELECT … FOR UPDATE SKIP LOCKED`
sous PostgreSQL), pose un heartbeat, écrit ses événements dans `run_events` (id croissant).

- **SQLite en mode WAL** par défaut (un serveur, quelques workers) ; **PostgreSQL** possible
  (mode serveur). SQLAlchemy 2 (paquet `store/`, partagé par l'API et les workers).
  Schéma créé de façon idempotente avec un numéro de version et des migrations Python ordonnées ;
  Alembic ne sera introduit que si les migrations deviennent complexes.
- Run dont le heartbeat expire → `interrompu`, relançable depuis le dernier cache valide.
- Annulation : drapeau en base relu par le worker (signal d'annulation du contexte).

## Conséquences
Un seul composant d'état, simple à sauvegarder. Limite : SQLite sur volume local uniquement
(pas de partage réseau) ; au-delà, passer à PostgreSQL.
