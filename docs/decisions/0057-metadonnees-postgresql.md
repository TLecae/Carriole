# ADR 0057 — Métadonnées du serveur dans PostgreSQL

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0004 (file de jobs dans les métadonnées), 0052 (mode serveur)

## Contexte
Les métadonnées (flux, runs, événements, sessions, partages, journal) sont dans SQLite : parfait sur un poste,
suffisant pour une petite équipe sur une seule machine. Pour plus d'utilisateurs, des sauvegardes gérées par
l'organisation, ou une base hors du conteneur, il faut un vrai serveur de base de données. Le store passe par
SQLAlchemy et devait « rester compatible PostgreSQL » (ADR 0020) sans que ce soit vérifié.

## Décision
1. `ETL_DB_URL` (ou `ETL_DB_URL_FILE`, fichier de secret, puisque l'adresse porte le mot de passe) choisit la base :
   `postgresql+psycopg://…` (pilote déjà livré avec le moteur). Sans réglage : SQLite, comme avant.
2. Compatibilité vérifiée : les tests du store et les scénarios du mode serveur (connexion, droits, journal) tournent
   aussi sur une base PostgreSQL jetable (`ETL_TEST_STORE_URL`), en local et dans un job de la CI.
3. Les chaînes saisies sont bornées à la taille de leur colonne (noms de flux, de runs, d'utilisateurs ; champs des
   connexions et planifications) : PostgreSQL refuse ce que SQLite accepte.
4. Les tables se créent et migrent au démarrage, comme avec SQLite ; la réservation d'un run reste une mise à jour
   conditionnelle (`status = 'queued'`), sûre entre plusieurs workers.

## Conséquences
- Le stockage des résultats (cache des runs) reste sur le disque du serveur : seules les métadonnées vont en base.
