# Sécurité : modèle de menace et mesures

Document de revue. Il dit ce qui est protégé, comment, et ce qui reste à la charge de l'utilisateur ou de
l'administrateur. Dernière révision : jalon 9 (application de bureau, [ADR 0020](decisions/0020-application-de-bureau.md)).

## Hypothèses
- L'atelier tourne **sur le poste de l'utilisateur**, avec ses droits Windows : il peut lire et écrire ce que
  l'utilisateur peut lire et écrire, rien de plus. Il n'est pas un service réseau.
- Un flux est du code déclaratif exécuté avec les droits de l'utilisateur : ouvrir un flux reçu d'un tiers revient à
  exécuter un programme (nœuds SQL, écriture de fichiers, plugins). Ne pas ouvrir de flux de source inconnue.
- Les **plugins** (fichiers Python) sont du code arbitraire exécuté avec les droits de l'utilisateur.

## Identification
Pas d'authentification : l'utilisateur est le compte Windows. L'identité sert aux quotas et au « mes runs », **pas aux
droits**. (Mode serveur futur : l'en-tête `X-ETL-User` posé par un proxy d'authentification.)

## Mesures en place

| Risque | Mesure | Où |
| --- | --- | --- |
| Un site web ouvert dans le navigateur appelle l'API locale (127.0.0.1) | écoute sur 127.0.0.1 seulement ; **jeton** aléatoire par démarrage (cookie `HttpOnly; SameSite=Strict`, ou en-tête `X-ETL-Token`) ; aucun CORS ; seule `/api/health` est publique | `etl_api.security`, [ADR 0020](decisions/0020-application-de-bureau.md) |
| « DNS rebinding » (un nom de domaine détourné vers 127.0.0.1) | l'en-tête `Host` doit être `127.0.0.1:<port>` ou `localhost:<port>` | `etl_api.security` |
| Un autre utilisateur de la machine appelle l'API | le jeton n'est écrit que dans `server.json` (dossier de l'utilisateur) et transmis à l'application | `etl_api.serve` |
| Page ou lien externe dans la fenêtre Electron | bac à sable, `contextIsolation`, pas de `nodeIntegration` ; navigation limitée à l'origine du backend ; liens http/https ouverts dans le navigateur ; dialogues natifs réservés à la page de l'atelier | `desktop/src/main.ts` |
| Un worker survit à son superviseur | les workers lisent leur entrée standard : sa fermeture les arrête proprement | `etl_worker.main` |
| Écriture accidentelle d'un fichier de flux par deux personnes | révision = empreinte du contenu : l'enregistrement est refusé si le fichier a changé ; écriture atomique (`.part` puis substitution) | [ADR 0021](decisions/0021-flux-et-composants-en-fichiers.md), `etl_engine.atomic` |
| Fichier de flux supprimé par l'application | jamais : « retirer de la liste » ne touche pas au fichier | `FileStore.delete_flow` |
| Injection SQL dans la clause WHERE de l'inspecteur | expression unique et équilibrée, DuckDB en lecture seule, accès externe coupé, configuration verrouillée, délai maximal | `api/data_service.py` |
| Injection SQL dans les expressions des modificateurs | mêmes contrôles, connexion DuckDB sans accès fichiers ni réseau | `etl_engine.expr` |
| Expression d'utilisateur composée dans une requête qui lit les ports (voie SQL, ports calculés à la demande) | connexion confinée après le chargement des extensions : elle ne lit que les fichiers des ports concernés et n'écrit que dans le run (`allowed_paths`, `allowed_directories`, accès externe coupé, configuration verrouillée) | `etl_engine.duck.confine`, [ADR 0044](decisions/0044-ports-calcules-a-la-demande.md) |
| Injection SQL par les valeurs des entités (DatabaseQueryPerFeature) | valeurs transmises comme **paramètres liés**, jamais collées au texte | `formats/pg.bind_placeholders` |
| Lecture d'un raster ou d'un catalogue STAC distant | un fichier local ou une adresse `http(s)://` (avec les droits de l'utilisateur) ; proxy du poste ; lecture par plages ; l'inspecteur ne lit que les rasters référencés par un run (`/tiles/raster/…`, `/api/data/…/raster/…`), jamais un chemin transmis par le client | `etl_engine.raster.core`, `etl_api.raster_service` |
| Une donnée sous convention part chez un fournisseur d'IA | par défaut un fournisseur distant ne reçoit que des **schémas** (jamais de valeur) ; « schéma + lignes » est un choix par fournisseur, visible en permanence dans le panneau ; le texte, le flux ouvert et les messages du journal sont toujours transmis (dit dans les réglages) | `etl_assistant.privacy`, `etl_assistant.tools` |
| Un modèle modifie un flux ou exécute du code sans accord | les outils d'action ne font que **proposer** : appliqué d'un clic (une modification annulable, refusée si le flux a changé) ; un modificateur écrit par le modèle n'est exécuté qu'au clic, d'abord en test dans un processus isolé (délai 90 s), puis installé s'il réussit | `etl_assistant.agent`, `etl_assistant.plugins` |
| Fuite d'une clé d'API | chiffrée en base (Fernet, clé protégée par le coffre Windows), jamais renvoyée par l'API ni écrite dans `settings.json` ; lue seulement pour appeler le fournisseur | `etl_store.chats`, `etl_api.routes.assistant` |
| Un modèle lit des fichiers hors des dossiers autorisés | `inspect_file` et `list_dir` passent par `etl_engine.paths` (mêmes garde-fous que les modificateurs) ; lecture seule | `etl_api.assistant_backend` |
| Écriture accidentelle en base par un flux de lecture | lecture PostgreSQL en transaction `READ ONLY` ; l'écriture est un choix explicite | `PostgresReader`, `DatabaseQuery` |
| Mots de passe de bases | connexions nommées, mot de passe chiffré (Fernet), jamais renvoyé par l'API ni écrit dans les journaux ni dans le flux | [ADR 0011](decisions/0011-secrets-et-connexions.md) |
| Clé de chiffrement copiée avec la base | la clé est protégée par **DPAPI** : illisible hors du compte Windows qui l'a créée. Le moteur la met alors de côté (`secret.key.illisible-…`) et en crée une autre : les mots de passe enregistrés sont à ressaisir | `etl_store.secretstore`, `etl_store.config` |
| Secrets du dépôt | `.env` non versionné, mots de passe chiffrés masqués dans les échantillons | `.gitignore` |
| Navigateur | en-têtes `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. La CSP n'ouvre `connect-src` / `img-src` qu'à trois serveurs de tuiles (OpenStreetMap, Esri, IGN) et à `tile_hosts` ; `Referrer-Policy: strict-origin-when-cross-origin` leur envoie l'origine de l'atelier (jamais le chemin) | `etl_api.security` |
| Ressources | concurrence globale et par utilisateur, délai maximal d'un run, budget mémoire (débordement sur disque), purge du cache (âge et taille), plafond d'entités par tuile | `ETL_MAX_RUNNING*`, `ETL_RUN_TIMEOUT`, `ETL_MEMORY_BUDGET_MB`, `ETL_CACHE_*` |
| Runs orphelins | heartbeat, passage en « interrompu », reprise depuis le cache | [ADR 0004](decisions/0004-file-de-jobs-dans-les-metadonnees.md) |
| Cache lisible par tous | l'inspecteur n'ouvre que le dossier du cache (`allowed_directories`) | `Duck` |

Mode serveur (non livré) : dossiers autorisés (`ETL_READ_ROOTS`, `ETL_WRITE_ROOTS`, [ADR 0016](decisions/0016-dossiers-autorises.md)),
proxy d'authentification devant l'API. L'écriture en deux temps sur les partages réseau est active sur le poste
([ADR 0019](decisions/0019-partage-reseau-lecture-ecriture.md)).

## À faire par l'utilisateur
1. N'ouvrir que des flux et ne déposer dans `plugins\` que du code **relu**.
2. Créer les comptes PostgreSQL **en lecture seule** pour les connexions de lecture.
3. Installer l'application avec son propre compte Windows (la clé de chiffrement et les connexions lui sont propres).

## Risques résiduels
- Pas de droits fins ni d'audit des actions : hors périmètre.
- Un plugin ou un nœud SQL (connexion + droits du compte) peut faire tout ce que ses droits permettent.
- Un programme malveillant qui s'exécute **sous le même compte Windows** peut lire `server.json` (jeton) et la clé de
  chiffrement (DPAPI protège contre un autre compte, pas contre le même) : la protection est celle de la session.
- L'application n'est pas signée : Windows SmartScreen avertit à l'installation ([ADR 0022](decisions/0022-electron-et-installeur.md)).
- Le texte des flux et les journaux peuvent contenir des chemins ou des noms de tables : ne pas y mettre de secret
  (utiliser les connexions nommées et `${env:NOM}`).
