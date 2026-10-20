# ADR 0011 — Secrets et connexions

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
- Configuration par variables d'environnement / `.env` (non versionné) ; en mode serveur, mots de passe de
  service par fichiers de secret (`*_FILE`). Jamais dans un installeur ni dans git.
- Connexions de bases partagées entre flux : table `connections` ; le mot de passe est
  chiffré (Fernet). Clé : `ETL_SECRET_KEY`, sinon `ETL_SECRET_KEY_FILE`, sinon un fichier
  `secret.key` créé (droits 0600) dans le dossier des métadonnées au premier besoin. L'API ne renvoie jamais
  le mot de passe ; si la clé est perdue, les mots de passe sont à ressaisir (message explicite).
- Les connexions sont résolues à l'exécution par un fournisseur installé par l'API et le worker
  (`etl_engine.connections`) ; sans fournisseur (CLI, tests), `ETL_CONN_<NOM>` (URI libpq).
- Un flux référence une connexion par son nom, pas par ses identifiants.
