# ADR 0016 — Dossiers autorisés pour les fichiers

- **Statut** : accepté pour le mode serveur ; sur le poste de bureau, pas de restriction ([ADR 0020](0020-application-de-bureau.md))
- **Date** : 2026-10-07

## Contexte
Un flux peut lire et écrire des fichiers par chemin. Sans garde-fou, tout utilisateur de l'atelier peut lire
n'importe quel fichier du serveur (configuration, secrets) ou en écraser.

## Décision
- `ETL_READ_ROOTS` et `ETL_WRITE_ROOTS` listent les dossiers accessibles (`etl_engine.paths`). Rien n'est lisible
  ailleurs : ni le cache, ni les métadonnées, ni `/etc`.
- Le chemin est **résolu avant contrôle** (`..` et liens symboliques) ; un motif de fichiers (`**/*.gpkg`) est
  contrôlé fichier par fichier.
- Contrôle dans les modificateurs d'entrée-sortie (`Reader`, `Writer`, `ComponentInput`), donc **à l'analyse comme à
  l'exécution** : l'éditeur signale « accès refusé » dès la saisie.
- Variables absentes (poste de dev, ligne de commande) : pas de restriction.
- Les mots de passe de bases passent par des connexions nommées chiffrées (ADR 0011), jamais par un chemin.

## Limites connues
- Un plugin Python est du code exécuté avec les droits du worker : le dossier de plugins est en lecture seule et
  reste réservé aux administrateurs. Les plugins ne sont pas confinés par cette règle.
- La règle protège les fichiers, pas le réseau : un flux peut interroger toute base joignable depuis le serveur
  (connexions nommées créées par les utilisateurs). À limiter au niveau du réseau.
