# Carriole sur un serveur

Plusieurs personnes utilisent Carriole dans leur navigateur, sur une machine partagée. Choix d'architecture :
[ADR 0052](decisions/0052-mode-serveur-et-authentification.md).

## Installer avec Docker

L'image (fichier `Dockerfile` à la racine du dépôt) contient Carriole, son interface, les extensions de DuckDB, les
grilles PROJ et bubblewrap. Elle démarre `etl serve --server` sur le port 8080, sous un utilisateur sans droits
(uid 10001), avec ses données dans le volume `/var/lib/carriole`.

Les réglages (variables `ETL_…` décrites plus bas) vont dans `docker/carriole.env`, non versionné : il contient un
mot de passe. Le copier depuis `docker/carriole.env.example`, y choisir le mot de passe du premier administrateur
(`ETL_ADMIN_PASSWORD`). Puis, dans le dossier `docker` :

```
docker compose up -d --build
```

Le dossier de la machine monté sur `/mnt/donnees` (le dossier autorisé) se choisit par `CARRIOLE_DONNEES` (défaut :
`docker/donnees`), par exemple le partage de fichiers de l'organisation déjà monté sur la machine. Pour garder les
métadonnées dans PostgreSQL : renseigner `POSTGRES_PASSWORD` et `ETL_DB_URL` dans `carriole.env`, puis
`docker compose --profile postgres up -d --build`. Placer un proxy HTTPS devant le port 8080 et déclarer son adresse
dans `ETL_TRUSTED_PROXIES`.

Le code Python des flux s'exécute dans bubblewrap, qui a besoin des espaces de noms utilisateur : le profil seccomp par
défaut de Docker les refuse, et certaines distributions les restreignent (Ubuntu 24.04 :
`kernel.apparmor_restrict_unprivileged_userns`). Sans eux, *PythonScript* le dit dès l'analyse du flux ;
`ETL_USER_CODE=deny` le désactive simplement.

## Démarrer sans Docker

Depuis une installation des sources (voir le README) :

```
etl serve --server
```

Le serveur écoute sur `ETL_HOST` (0.0.0.0) et `ETL_PORT` (8080). Il refuse de démarrer sans aucun compte ni
`ETL_ADMIN_PASSWORD`, et dit quoi régler. Placer un proxy HTTPS (nginx, Caddy, Traefik…) devant lui : les mots de passe ne doivent
pas circuler en clair.

Chaque réglage se lit dans la variable `ETL_<CLÉ>`, sinon dans `settings.json` (clé en minuscules, sans `ETL_`).
Les mots de passe ne se lisent que dans l'environnement ou dans un fichier (`…_FILE`, par exemple un secret Docker).

## Base des métadonnées

Flux, runs, sessions, partages et journal sont dans SQLite (dossier des métadonnées) par défaut : suffisant pour une
petite équipe. Pour une base PostgreSQL ([ADR 0057](decisions/0057-metadonnees-postgresql.md)) :

| Variable | Rôle |
| --- | --- |
| `ETL_DB_URL` | `postgresql+psycopg://carriole:motdepasse@db.exemple.org/carriole` |
| `ETL_DB_URL_FILE` | la même adresse dans un fichier de secret (elle porte le mot de passe) |

Les tables se créent au premier démarrage. Les résultats des runs restent sur le disque du serveur (`ETL_CACHE_DIR`).

## Comptes et connexion

Chaque utilisateur se connecte avec un identifiant et un mot de passe : des comptes locaux, gérés par les
administrateurs dans *Outils → Comptes* (création, nom affiché, groupes, nouveau mot de passe, suppression,
[ADR 0058](decisions/0058-comptes-locaux.md), [ADR 0059](decisions/0059-comptes-locaux-seulement.md)). Chacun change son
mot de passe depuis son menu. Mots de passe de 8 caractères au moins, hachés (scrypt) dans `ETL_USERS_FILE`
(`users.json` du dossier des métadonnées).

| Variable | Rôle |
| --- | --- |
| `ETL_ADMIN_PASSWORD` ou `ETL_ADMIN_PASSWORD_FILE` | premier démarrage sans compte : crée le compte administrateur ; ensuite sans effet |
| `ETL_ADMIN_USER` | son identifiant (`admin`) ; il est mis dans le premier groupe de `ETL_ADMINS` (`carriole-admins`) |
| `ETL_ADMINS` | administrateurs : noms et groupes séparés par des virgules (`dupont,group:sig-admins`) ; par défaut `group:carriole-admins` |
| `ETL_SESSION_HOURS` | durée d'une session sans activité (12) |
| `ETL_TRUSTED_PROXIES` | adresses ou réseaux du proxy HTTPS placé devant le serveur (`127.0.0.1,::1`) : seul lui transmet l'adresse du client et le schéma HTTPS |
| `ETL_SECURE_COOKIE` | `1` : cookie de session `Secure` même si le proxy ne transmet pas `X-Forwarded-Proto` |
| `ETL_ALLOWED_HOSTS` | noms d'hôte acceptés (`carriole.exemple.org:443`), facultatif |

En ligne de commande (facultatif) : `etl users add dupont --display "Jean Dupont" --group carriole-admins`,
`etl users list`, `etl users remove dupont`.

## Administrateurs

Seuls les administrateurs (`ETL_ADMINS`) modifient les réglages de la machine dans *Paramètres* (calcul, réseau,
composants, variables globales, fournisseurs de l'assistant), installent un modificateur écrit par l'assistant et
arrêtent le serveur. Les autres utilisateurs consultent ces réglages. Chacun garde sa langue d'interface.
L'onglet *Paramètres → Serveur* montre comment le serveur est réglé.

## Journal d'activité

*Outils → Journal d'activité* (administrateurs) : connexions et échecs de connexion, flux créés, enregistrés,
supprimés ou partagés, connexions aux bases, runs lancés ou arrêtés, planifications, réglages modifiés, avec
l'utilisateur, la date et l'adresse ([ADR 0056](decisions/0056-journal-d-activite.md)). Ni mot de passe ni contenu de
flux. Gardé `ETL_AUDIT_DAYS` jours (365 ; 0 : sans limite).

## Partage de la machine

Les runs attendent leur tour dans une file équitable : passe d'abord celui de l'utilisateur qui a le moins de runs en
cours ([ADR 0055](decisions/0055-file-equitable-et-quotas.md)). Réglages dans *Paramètres → Calcul* (administrateurs) :

| Variable | Rôle | Défaut |
| --- | --- | --- |
| `ETL_MAX_RUNNING_PER_USER` | runs simultanés d'un utilisateur (0 : pas de limite) | 2 |
| `ETL_USER_MEMORY_MB` | mémoire que tous les runs d'un utilisateur se partagent | pas de limite propre |
| `ETL_USER_CPU_BUDGET` | cœurs que tous les runs d'un utilisateur se partagent | pas de limite propre |

## Partage

Chacun ne voit que ses flux, ses connexions et ses runs, plus ce qu'on lui partage
([ADR 0054](decisions/0054-droits-et-partage.md)). *Partager…* (carte de l'accueil, menu *Fichier*, fenêtre
*Connexions*) donne à un utilisateur, à un groupe ou à tout le monde :

| Droit | Flux | Connexion |
| --- | --- | --- |
| Lecture | ouvrir, lancer, inspecter ses runs ; *Enregistrer* en fait une copie à son nom | l'utiliser dans un flux, voir ses tables |
| Modification | en plus : enregistrer, arrêter ou mettre en pause ses runs | en plus : changer ses réglages |

Supprimer et partager restent au propriétaire et aux administrateurs, qui voient tout. Les composants sont
utilisables par tous. Une planification tourne avec les droits de son créateur, revérifiés à chaque échéance.

## Code Python des flux

*PythonScript* exécute du code écrit dans un flux. Sur un serveur, il tourne par défaut dans un processus isolé par
bubblewrap ([ADR 0053](decisions/0053-code-python-isole.md)) : il ne voit ni les fichiers, ni la base, ni le réseau.

| Variable | Rôle |
| --- | --- |
| `ETL_USER_CODE` | `sandbox` (défaut du serveur) : processus isolé ; `deny` : *PythonScript* refusé ; `allow` : dans le worker, avec les droits du serveur (à réserver à des utilisateurs de confiance) |
| `ETL_SANDBOX` | `bwrap` (défaut, paquet `bubblewrap`) ; `process` : sans isolation (essais) ; ou la commande d'un autre outil d'isolation |

bubblewrap a besoin des espaces de noms utilisateur (*user namespaces*). Dans un conteneur Docker, le profil seccomp
par défaut les refuse : lancer le conteneur avec un profil qui les permet, ou garder `ETL_USER_CODE=deny`.

## Fichiers

En mode serveur, Carriole ne lit et n'écrit que dans les dossiers autorisés ; sans eux, aucun fichier n'est
accessible. Monter les partages de l'organisation sur le serveur, puis les déclarer :

| Variable | Rôle |
| --- | --- |
| `ETL_READ_ROOTS` | dossiers lisibles, séparés par `:` (`/mnt/donnees:/mnt/referentiels`) |
| `ETL_WRITE_ROOTS` | dossiers où l'on peut écrire |
| `ETL_ROOT_LABELS` | libellés montrés dans l'explorateur : `chemin=libellé` séparés par `\|` (`/mnt/donnees=Données (Z:)`) |
