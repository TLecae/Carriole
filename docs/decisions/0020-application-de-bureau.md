# ADR 0020 — Application de bureau Windows

- **Statut** : accepté
- **Date** : 2026-10-11

## Contexte
Les utilisateurs travaillent sous Windows sur les fichiers du partage de l'équipe (`Z:`, `\\serveur\partage`). Un
programme installé sur le poste lit et écrit ces fichiers avec les droits de l'utilisateur, sans serveur à administrer.

## Décision
Un **programme Windows** installé par un double-clic sur un `.exe` ([ADR 0022](0022-electron-et-installeur.md)) :

```
Electron (desktop/)  fenêtre, icône de notification, dialogues natifs de fichiers
        │ lance ou rejoint
        ▼
etl serve (Python)  127.0.0.1:<port libre>, jeton obligatoire
   ├─ API FastAPI, qui sert aussi l'interface compilée
   ├─ N workers : processus fils supervisés, relancés s'ils plantent
   └─ planificateur (fil d'un worker)
etl (ligne de commande, facultative)  exécute directement, ou --submit vers le backend en marche
```

- **Moteur** : lots Arrow, un GeoParquet par port, voie SQL DuckDB, threads, file de runs dans SQLite
  ([ADR 0003](0003-moteur-arrow-geoparquet-duckdb.md), [0004](0004-file-de-jobs-dans-les-metadonnees.md),
  [0013](0013-voies-execution-et-parallelisme.md)). Le banc à 1 M d'entités est consigné au journal (J9).
- **Dossiers** (`etl_engine.settings`) : données locales (base, cache, plugins, journaux) dans
  `%LOCALAPPDATA%\AtelierETL`, jamais sur un partage (SQLite) ; réglages dans `%APPDATA%\AtelierETL\settings.json`.
  `ETL_HOME` déplace les deux (tests, essais). Priorité : variable `ETL_*` > `settings.json` > défaut. Les réglages se
  modifient dans l'interface (bouton *Paramètres*, route `/api/settings`) ; `etl config` fait la même chose en ligne de
  commande. Le proxy et le budget mémoire sont transmis au backend et à ses workers par l'environnement
  (`settings.apply_environment`).
- **Un backend par utilisateur** : verrou de fichier (relâché par le système si le processus meurt) et `server.json`
  (adresse, jeton) que l'application et `etl` lisent pour le retrouver.
- **Sécurité du backend local** ([securite.md](../securite.md)) : écoute sur 127.0.0.1 ; jeton aléatoire par démarrage
  (cookie `HttpOnly; SameSite=Strict`, ou en-tête `X-ETL-Token` pour la ligne de commande) ; contrôle de l'en-tête
  `Host` contre le « DNS rebinding » ; pas de CORS. Indispensable : l'API exécute du Python (plugins).
- **Workers** : sous-processus pilotés par leur **entrée standard** (`stop`, ou fermeture = arrêt propre). Pas de
  `CTRL_BREAK` : sans console (application lancée en arrière-plan) il n'est pas délivré. Un superviseur disparu
  n'abandonne donc pas de worker. Relance avec attente croissante si un worker plante en boucle.
- **Utilisateur** = compte Windows. Le chemin d'un flux est celui que voit l'utilisateur (`Z:\…` ou `\\serveur\…`).
- **Partages réseau** : l'écriture se fait en deux temps (local puis publication d'un bloc) pour tout chemin UNC ou
  lecteur mappé ([ADR 0019](0019-partage-reseau-lecture-ecriture.md)).
- **Secrets** : la clé de chiffrement des connexions est protégée par DPAPI (lisible par le seul compte Windows).
- **Extension spatiale DuckDB** chargée par un point unique (`etl_engine.duck`) ; `ETL_DUCKDB_EXTENSIONS` désigne le
  dossier préinstallé par l'installeur (aucun téléchargement).
- **CI** : `scripts/ci.ps1` (ruff, pytest natif, vitest, build, Playwright contre un vrai `etl serve`). Les scénarios
  d'arrêt, de worker tué et de reprise sont des tests pytest (`api/tests/test_background_runs.py`).

## Cible future : Linux et cloud (non codées, à ne pas empêcher)
1. Le moteur, le store et l'API ne dépendent ni d'Electron ni de Windows ; le code propre au poste est confiné dans
   `desktop/` et `etl_api.serve`.
2. Toute la logique métier passe par HTTP + SSE ; l'IPC Electron ne sert qu'aux dialogues natifs et à la fenêtre.
3. Les fichiers passent par `paths` / `staging`, prêts à recevoir des URI (`s3://` : DuckDB et GDAL les lisent).
4. Les secrets passent par `etl_store.secretstore` (DPAPI sur le poste ; variables ou gestionnaire de secrets en
   serveur : `ETL_SECRET_KEY`, `ETL_SECRET_KEY_FILE`).
5. Le store reste compatible PostgreSQL ; `ETL_FLOW_STORAGE=db` garde les flux dans la base (serveur) et
   `X-ETL-User` reste l'identité posée par un proxy d'authentification.
6. `ETL_READ_ROOTS`, `ETL_WRITE_ROOTS` et `ETL_STAGED_ROOTS` restent disponibles pour un serveur ; aucun séparateur de
   chemin n'est codé en dur ; le moteur est testé sous Linux.

## Conséquences
- [ADR 0016](0016-dossiers-autorises.md) (dossiers autorisés) ne s'applique plus qu'au mode serveur.
- Sans signature de code, SmartScreen avertit à l'installation. La mise à jour est une réinstallation.
- Des antivirus d'entreprise peuvent ralentir l'écriture du cache : à mesurer sur les postes de l'équipe.
