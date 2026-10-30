# Installation et exploitation (poste de bureau)

Carriole est une application de bureau (Windows, Linux) : elle lit et écrit vos fichiers (disques locaux, `Z:`, `\\serveur\partage`) avec
vos droits, sans serveur à administrer. Choix d'architecture : [ADR 0020](decisions/0020-application-de-bureau.md).

## Installer (utilisateur)

Double-cliquer sur `Carriole-Setup-<version>.exe`. **Aucune ligne de commande n'est nécessaire.**

1. Windows demande l'autorisation d'administrateur (fenêtre habituelle) : répondre **Oui**. L'installation est faite
   une fois pour tous les utilisateurs du poste, dans `C:\Program Files\Carriole`.
2. L'assistant propose la **langue** (français ou anglais), le **dossier d'installation**, puis l'option **« Laisser
   Carriole tourner en arrière-plan à la fermeture de la fenêtre »** (décochée par défaut) ; cliquer sur *Installer*.
3. Les raccourcis (bureau, menu Démarrer) sont créés et l'application se lance. Tout le nécessaire est embarqué
   (Python, GDAL, extension spatiale de DuckDB, grilles de transformation françaises) : **aucun accès à internet
   n'est requis** pour fonctionner.

- **SmartScreen** : le programme n'est pas signé. Windows affiche « Windows a protégé votre ordinateur » : cliquer
  « Informations complémentaires », puis « Exécuter quand même ».
- **Première ouverture** plus lente (analyse antivirus des ~900 Mo installés).
- **Mise à jour** : lancer l'installeur plus récent ; il remplace l'ancienne version (le moteur en cours est arrêté
  proprement, les runs interrompus se reprennent depuis leur cache). **Désinstaller** : *Paramètres Windows →
  Applications* ; les données et réglages de chaque utilisateur sont conservés.
- **Ouverture** : une petite fenêtre « Ouverture en cours… » s'affiche le temps que le moteur démarre.
- **Fermeture** : par défaut, fermer la fenêtre **arrête complètement** Carriole (moteur compris) ; si des runs sont en
  cours, une question propose de les laisser finir. Avec l'option **arrière-plan** (choisie à l'installation, modifiable
  dans *Paramètres → Général*), fermer la fenêtre la masque (icône dans la zone de notification) pour que les
  planifications et les runs continuent ; *Quitter* (clic droit sur l'icône) arrête alors le moteur. Un double-clic
  sur un fichier de flux l'ouvre dans la fenêtre déjà ouverte.

### Linux (AppImage)

Télécharger `Carriole-<version>-x86_64.AppImage` ([page des versions](https://github.com/TLecae/Carriole/releases/latest)),
le rendre exécutable puis le lancer (pas de droits d'administrateur, rien d'autre à installer) :

```bash
chmod +x Carriole-*-x86_64.AppImage
./Carriole-*-x86_64.AppImage
```

- Si rien ne s'ouvre : installer FUSE 2 (`sudo apt install libfuse2t64`, ou `libfuse2` avant Ubuntu 24.04). Sous
  Ubuntu 24.04 et plus, si Chromium refuse de démarrer (« sandbox »), lancer avec `--no-sandbox`.
- Données dans `~/.local/share/carriole`, réglages dans `~/.config/carriole` (variables XDG respectées). La clé de
  chiffrement des secrets est un fichier lisible par vous seul (droits 0600) : pas de coffre système comme sous
  Windows.
- Fermer avec des runs en cours et choisir « Laisser tourner » garde Carriole sans fenêtre jusqu'à la fin des runs.
- Ligne de commande : `etl` est dans l'AppImage (`./Carriole-….AppImage --appimage-extract` puis
  `squashfs-root/resources/bin/etl`) ; plus simple en développement (`uv sync`).

### Construire l'installeur (administrateur du projet)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1          # desktop\release\Carriole-Setup-<version>.exe
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -DirOnly  # seulement le dossier win-unpacked (essais)
```

Linux : `scripts/build_desktop.sh` (`desktop/release/Carriole-<version>-x86_64.AppImage`). Versions publiées : une
étiquette `v*` poussée sur GitHub construit les deux installeurs et les joint à la version (`.github/workflows/release.yml`).

Le build télécharge une fois l'extension DuckDB, les grilles PROJ, Electron et NSIS. Architecture :
[ADR 0022](decisions/0022-electron-et-installeur.md). Le bytecode Python est livré avec l'installation : le dossier du
programme reste en lecture seule pour les utilisateurs.

Les rasters ([ADR 0026](decisions/0026-rasters.md)) viennent avec `rasterio`, dont la roue Windows embarque GDAL et ses pilotes (GeoTIFF, COG, JP2, ASC, VRT, NetCDF) : rien à installer sur le poste. Les grilles PROJ françaises préinstallées (`PROJ_USER_WRITABLE_DIRECTORY`) servent aussi aux rasters. Les catalogues STAC et les COG distants passent par le proxy du poste ; hors ligne, seuls les rasters locaux sont lus.

L'assistant IA ([ADR 0027](decisions/0027-assistant-ia.md)) appelle le fournisseur choisi par le proxy du poste ; un modèle local (Ollama, `http://localhost:11434/v1`) ne sort pas du poste. Les clés d'API se saisissent dans l'application (*Paramètres → Assistant*) ; elles sont chiffrées avec la clé protégée par le coffre de Windows.

## Régler l'application (Paramètres)

Le bouton **Paramètres** (en haut à droite) regroupe tous les réglages ; ils s'appliquent sans toucher à un fichier
ni à une ligne de commande.

| Onglet | Réglages |
| --- | --- |
| Général | langue de l'interface et des messages ; nom d'utilisateur (signe les flux) ; e-mail de fin de run ; démarrer avec Windows ; accès aux dossiers de données et de journaux |
| Calcul | nombre de runs en parallèle (workers) ; mémoire maximale du moteur (au-delà, DuckDB déborde sur disque) |
| Réseau | proxy (vide : celui de Windows) ; serveurs de tuiles de fond de plan supplémentaires |
| Composants | dossiers de composants partagés (voir ci-dessous) |
| Variables globales | variables `${global:NOM}` communes à tous les flux |
| Assistant | fournisseurs de modèles et clés d'API |

Les réglages de l'onglet *Calcul* et le proxy s'appliquent au prochain démarrage de l'application (bouton *Redémarrer
maintenant* proposé). Ils sont enregistrés dans `settings.json` ; l'application les relit seule.

## Où sont les données

| Quoi | Où |
| --- | --- |
| Base locale (runs, planifications, connexions, conversations et clés d'API de l'assistant — chiffrées), cache, plugins, journaux, clé de chiffrement | `%LOCALAPPDATA%\Carriole` (jamais sur un partage : SQLite) |
| Réglages | `%APPDATA%\Carriole\settings.json` |
| Flux et composants | **des fichiers JSON**, où vous les rangez (typiquement le partage de l'équipe) |

Les variables `ETL_*` l'emportent sur `settings.json`, qui l'emporte sur les valeurs par défaut.

## Composants partagés

Un composant est un flux qui déclare `component`, rangé dans un **dossier de composants** (*Paramètres → Composants*, ou
variable `ETL_COMPONENTS_DIR`, plusieurs dossiers séparés par `;`). Pointer tous les postes vers le même dossier du
partage les partage. Les versions publiées sont des fichiers voisins `<nom>.v<N>.json` (immuables : ne pas les
modifier à la main) — [ADR 0021](decisions/0021-flux-et-composants-en-fichiers.md).

## Ligne de commande (facultative)

L'application se suffit à elle-même ; la commande `etl` sert à **automatiser** (planificateur de tâches, scripts). Elle est
installée avec l'application et ajoutée au PATH de la machine (ouvrir un nouveau terminal). Les réglages s'y consultent
aussi : `etl config get`, `etl config set workers 3`.

| Commande | Rôle |
| --- | --- |
| `etl run flux.json -p CLE=valeur` | exécute un flux (codes de sortie : 0 terminé, 1 erreur, 2 annulé, 3 usage) |
| `etl run flux.json --each "D:\dossier\*.shp" --as SRC [--parallel 4]` | un run par fichier (aussi `--each-list liste.txt`, `--each-csv params.csv`) ; code de sortie non nul si l'un échoue |
| `etl run flux.json --submit [--wait]` | met le run dans la file du backend (visible dans l'interface) ; `--wait` attend la fin |
| `etl run flux.json --log-file run.log` | garde aussi le journal des événements |
| `etl validate flux.json` | vérifie un flux sans l'exécuter |
| `etl inspect fichier.gpkg [--layer X] [--count]` | format, couches, champs, système de coordonnées |
| `etl runs list / show ID / cancel ID` | runs du backend en marche |
| `etl connections list / add / test` | connexions aux bases (mot de passe saisi sans écho) |
| `etl config get / set / unset / path` | réglages |
| `etl serve / status / stop / open` | backend local |
| `etl modifiers list / doc`, `etl new-modifier` | catalogue et outillage pour écrire un modificateur |

Pour planifier avec le **Planificateur de tâches Windows** plutôt qu'avec la page *Planification* de l'application :
action « Démarrer un programme » → `etl.exe run D:\flux\nuit.json -p ANNEE=2026 --log-file C:\Logs\nuit.log`. Préférer les
chemins UNC (`\\serveur\partage`) aux lettres de lecteur : une tâche qui s'exécute hors de votre session ne voit pas
les lecteurs mappés. `--submit --wait` fait passer le run par le backend (visible dans l'interface, mêmes quotas).

## Fonds de plan de la carte

La carte de l'inspecteur affiche des tuiles d'**OpenStreetMap**, d'**Esri** (satellite) et de l'**IGN**
(photographie aérienne). Elles sont chargées **par l'interface** (pas par le moteur), donc le poste doit accéder à
`tile.openstreetmap.org`, `server.arcgisonline.com` et `data.geopf.fr` en HTTPS. La politique `Content-Security-Policy`
(`etl_api/security.py`) les autorise : **sans cela les tuiles sont bloquées et la carte reste grise**.

Pour un serveur de tuiles interne : l'ajouter dans *Paramètres → Réseau* (ou variable `ETL_TILE_HOSTS`), puis le saisir
dans le ⚙ de la carte, option « Adresse personnalisée » (`https://tuiles.exemple.fr/{z}/{x}/{y}.png`). Hors ligne : option « Aucun ». Respecter les conditions
d'utilisation de chaque fournisseur.

## Performance : parallélisme et mémoire

| Variable | Rôle |
| --- | --- |
| `ETL_BATCH_WORKERS` | lots traités en parallèle dans un nœud « au fil de l'eau » (moitié des cœurs du run, 2 à 8) |
| `ETL_DUCK_THREADS` | threads de chaque connexion DuckDB « lourde » (les cœurs du run par défaut) |
| `ETL_MEMORY_BUDGET_MB` | budget mémoire du moteur (*Paramètres → Calcul*, 75 % du poste par défaut), partagé entre les runs en cours ; DuckDB en reçoit 60 % (`memory_limit`, partagés entre les nœuds lourds du run : un seul les reçoit entiers), le reste va aux lots Arrow (leur taille en découle) |
| `ETL_CACHE_DIR` | dossier du cache des runs, où les calculs débordent aussi au-delà du budget mémoire (*Paramètres → Calcul → Dossier du cache* ; `%LOCALAPPDATA%\Carriole\cache` par défaut) : un disque local avec de la place |
| `ETL_BLOCKING_NODES` | nœuds qui chargent toutes leurs données exécutés en même temps dans un run (2) |
| `ETL_PIPELINE=0` | désactive le branchement en direct des nœuds (chacun attend la fin du précédent) : pour diagnostiquer |
| `ETL_PORT_COMPRESSION` | compression des ports intermédiaires (`zstd` par défaut ; `lz4` ou `snappy` écrivent plus vite, en plus gros) |
| `ETL_SQL_ROUTE=0` | désactive la voie SQL (tout passe par Python) : utile pour comparer ou diagnostiquer |

Un poste partage ses cœurs et sa mémoire entre `workers` runs simultanés (*Paramètres → Calcul → Runs en parallèle*,
3 par défaut) : chaque run reçoit sa part du budget (divisé par le nombre de runs en cours) et des cœurs. Si la
mémoire du poste est épuisée, le run s'arrête avec un message qui nomme le nœud concerné, au lieu de faire planter
le programme. Un traitement qui charge tout (superposition, fusion de polygones…) est le premier à manquer de
mémoire : filtrer en amont ou augmenter le budget.
Les tris, jointures attributaires et statistiques s'exécutent en SQL (DuckDB) et **débordent sur disque** au-delà du
budget mémoire, dans `cache\<run>\<nœud>\.spill` : prévoir de la place sur `%LOCALAPPDATA%`. Voir
[l'ADR 0013](decisions/0013-voies-execution-et-parallelisme.md). Un antivirus qui analyse `%LOCALAPPDATA%\Carriole`
ralentit les écritures : demander son exclusion si les temps se dégradent.

## Runs, rétention et notifications

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ETL_MAX_RUNNING`, `ETL_MAX_RUNNING_PER_USER` | nombre de workers, aucune limite | runs simultanés (poste de bureau : un seul utilisateur) ; 4 et 2 en mode serveur |
| `ETL_RUN_TIMEOUT` | aucun | durée maximale d'un run (secondes) |
| `ETL_CACHE_RETENTION_DAYS`, `ETL_CACHE_MAX_GB` | 14, 50 | purge du cache (âge et taille) |
| `ETL_STALE_SECONDS` | 30 | délai sans signe de vie après lequel un run est déclaré « interrompu » |
| `ETL_SMTP_HOST`, `ETL_SMTP_PORT`, `ETL_SMTP_USER`, `ETL_SMTP_PASSWORD`, `ETL_SMTP_FROM` | – | e-mail de fin de run (facultatif) |

## Connexions aux bases et clé de chiffrement

Les mots de passe des connexions sont chiffrés (Fernet). La clé est créée au premier besoin dans
`%LOCALAPPDATA%\Carriole\meta\secret.key`, **protégée par Windows (DPAPI)** : seul votre compte Windows peut la
relire, sur ce poste. Copier le fichier ailleurs ne sert à rien ; en cas de changement de poste, ressaisir les
mots de passe. Le poste doit atteindre les serveurs PostgreSQL de l'entreprise (proxy et pare-feu habituels).

## Mode serveur (Linux, cloud)

Non livré, mais prévu : `ETL_FLOW_STORAGE=db` garde les flux dans la base, `ETL_READ_ROOTS` et `ETL_WRITE_ROOTS`
restreignent les dossiers, `X-ETL-User` porte l'identité posée
par un proxy d'authentification (voir [ADR 0020](decisions/0020-application-de-bureau.md), section « Cible future »).

## Dépannage

| Symptôme | Cause probable |
| --- | --- |
| « Le backend n'est pas démarré » | lancer l'application, ou `etl serve` ; `etl status` |
| Interface « accès refusé » (401) | ouvrir l'application par `etl open` (le jeton change à chaque démarrage) |
| Un worker redémarre en boucle | lire `%LOCALAPPDATA%\Carriole\logs\worker-N.log` |
| Carte grise | fonds de plan bloqués : voir plus haut |
| Run « interrompu » après un redémarrage | normal : *Reprendre* repart du cache |

## Installer pour développer ou essayer

Prérequis : [uv](https://docs.astral.sh/uv/), Node.js 22 ou plus.

```powershell
uv sync                                  # environnement Python (.venv), moteur et API
cd frontend; npm ci; npx vite build      # interface compilée (frontend\dist)
.\.venv\Scripts\etl.exe open             # démarre le backend et ouvre l'interface dans le navigateur
cd desktop; npm ci; npm start            # ou : la fenêtre Electron (utilise .venv)
```

`etl open mon_flux.json` ouvre directement un flux. `etl status` dit si le backend tourne, `etl stop` l'arrête (un run
en cours est interrompu proprement et reprenable). Développement de l'interface avec rechargement à chaud :
`etl serve` dans un terminal, `cd frontend; npm run dev` dans un autre (Vite proxifie `/api` et `/tiles` vers l'adresse de `etl status`, réglable par `VITE_API_URL`).
