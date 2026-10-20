# Journal des jalons

## Code ouvert, Linux, retrait de SQL Server (2026-10-20)

- Publication sous licence MIT, version Linux (AppImage), logo « i/o », site vitrine : [ADR 0039](decisions/0039-code-ouvert-et-linux.md).
- SQL Server retiré (lecteur, écrivain, type de connexion, pilote `mssql-python`) : son pilote ODBC, sous licence
  Microsoft, ne se redistribue qu'à des conditions incompatibles avec une diffusion libre et gratuite simple
  ([ADR 0040](decisions/0040-retrait-de-sql-server.md)). Restent PostgreSQL / PostGIS, Oracle et SQLite / SpatiaLite.
  104 modificateurs.
- « Transformers » devient **modificateurs** (« modifiers » dans le code et en anglais) ; noms techniques originaux,
  inspirés des outils SIG (QGIS) plutôt que de FME ; ports `requestor` / `supplier`, `clippee` / `clipper` renommés ;
  anciens flux migrés à la lecture, anciens plugins acceptés : [ADR 0041](decisions/0041-modificateurs.md).
- Site et README : bibliothèques libres sur lesquelles repose Carriole.

## Ouverture, fermeture et lecture limitée (2026-10-14)

- Petite fenêtre « Ouverture en cours… » pendant le démarrage du moteur (pas au démarrage masqué avec Windows).
- Fermer la fenêtre arrête complètement Carriole par défaut ; option « Laisser Carriole tourner en arrière-plan » dans
  l'installeur (page *Options*, `HKLM\Software\Carriole\KeepRunning`) et dans *Paramètres → Général* (`keep_running`
  de settings.json, qui l'emporte).
- Tous les lecteurs (fichiers, SQL Server, Oracle, PostgreSQL) : « Lignes lues au plus » (`max_rows`, 0 = toutes) ;
  la lecture s'arrête là (fichier ou curseur fermé) ; pastille « ≤ N » sur le nœud, avec bulle d'aide.

## Carte fluide ; « jusqu'au nœud » avec le cache ; bandeau du nœud (2026-10-14)

- Tuiles indépendantes de la vue (choix des entités par métatuile de 4 × 4 tuiles), index des entités trié Hilbert
  (comptage d'une zone : 0,02 à 0,2 s au lieu de 1,8 s), tuiles des zooms 0 à 10 calculées d'avance (247 tuiles en
  35 s pour 1,6 million de parcelles ; 7 ms par tuile ensuite au lieu de 0,3 à 1,4 s), gzip ; filtre ou couleur sans
  carte vide (`setTiles`), pas de fondu.
- « Jusqu'au nœud » : lectures de base reprises (`reusable = "partial"`), nœuds des autres branches gardés dans le
  run.
- Bandeau d'icônes au-dessus du nœud sélectionné (lancer jusqu'ici, désactiver, données) ; clic sur un nœud fiable ;
  contour de sélection renforcé. ADR 0038.
- Ensuite : le bandeau n'a plus de bouton *données* (un clic sur le nœud affiche l'onglet *Données*) ; la carte ne
  dit plus que « Chargement de la carte… » (plus de comptes d'entités ni de « Zoomez ») ; le port consulté passe
  avant les tuiles et la vue du précédent (tuiles annulées abandonnées, calcul d'avance du seul dernier port).

## Inspecteur : toutes les entités, les plus grandes de la vue, table copiable (2026-10-13)

- Plus de champ « Chargées » ni de N : table sur tout le port (pages au fil du défilement), un seul compte.
- Carte préparée en arrière-plan (`map_status`), message « Préparation de la carte… », ou pourquoi elle n'est pas
  affichée (mémoire) ; 34 s pour 1,6 million de parcelles, une seule fois.
- Vue v3 : plus de bandes à petite échelle (un point par entité et par pixel, carré d'un pixel dans la tuile),
  simplification au demi-pixel ; `esize` par ligne.
- Au-delà de 50 000 entités à l'écran : les plus grandes (classes de taille, stables entre tuiles et zooms) ; points
  échantillonnés. Comptage 1,8 s sur 1,6 million.
- Table : colonnes redimensionnables (double-clic : ajuster), copie par clic droit (valeur, ligne, nom de colonne)
  et `Ctrl+C`. ADR 0037.

## SQL Server, Oracle, SQLite / SpatiaLite (2026-10-13)

[ADR 0036](decisions/0036-sql-server-oracle-sqlite.md). Quatre lecteurs et écrivains de plus :
- SQL Server et Oracle, par connexions typées (type de base dans *Connexions*, authentification Windows possible pour
  SQL Server) ;
- SQLite / SpatiaLite, comme un fichier.

DatabaseQuery et DatabaseQueryPerFeature acceptent toutes les bases. Les pilotes sont livrés avec Carriole : rien à installer.

Mesures :
- SQL Server : 200 000 points écrits en 9,8 s par chargement en masse (6 fois plus vite que des INSERT) ;
- Oracle : 100 000 lignes en 1 s.

Corrigé en chemin : sur ce poste, numexpr chargeait le `msvcp140.dll` système (14.13, ancien) et le pilote SQL Server
plantait le moteur à son chargement ; le moteur charge d'abord la version récente. Oracle Spatial reste à essayer sur
une vraie base (image de test sans Spatial).

## Cache gardé tant que le flux est ouvert (2026-10-13)

[ADR 0035](decisions/0035-cache-garde-tant-que-le-flux-est-ouvert.md). Sur le poste de bureau, seules restent les
données des runs en cours et du dernier run de chaque flux ouvert. Les pages signalent leur run toutes les 20 s ; le
moteur supprime le reste toutes les 15 s. Après un plantage de l'appli ou du moteur, le nettoyage se fait au
redémarrage, après 90 s de grâce. Le cache du poste atteignait 12 Go.

Aussi : un port lu d'un GeoParquet garde ses métadonnées « geo », et DuckDB lisait sa géométrie en GEOMETRY
(« ARRAY_SLICE… » dans l'inspecteur) ; cette conversion est désactivée. Les catégories de la palette sont repliées à
l'ouverture.

## Inspecteur sur entités énormes, moteur surveillé (2026-10-13)

[ADR 0034](decisions/0034-carte-par-morceaux-et-moteur-surveille.md). Sur 24 parcelles RPG fusionnées (Occitanie,
485 Mo ; 10,7 M de sommets et 313 000 morceaux pour la plus grosse), chaque tuile simplifiait l'entité d'un bloc :
668 s pour GEOS. La vue de la carte a maintenant une ligne par morceau et par niveau de zoom ; les petits polygones
deviennent des cellules d'un pixel. Résultats :
- une tuile se calcule en 0,1 à 0,5 s à tous les zooms ;
- sélectionner une entité prend 0,4 s et renvoie 0,7 Mo (contre 4,7 s et 40 Mo) ;
- la vue se construit en 66 s, une seule fois.

La carte dessine au plus 50 000 entités de la vue. Au-delà, elle affiche un échantillon, le même à tous les zooms,
et le message « Zoomez pour tout voir ».

Le moteur tient un journal (`logs/server.log`, pile des plantages natifs) :
- l'application le relance s'il s'arrête ou bloque, à la même adresse et avec le même jeton ;
- la page affiche « reconnexion », puis refait l'enregistrement demandé pendant la coupure.

## Mise en cache globale (2026-10-13)

Comme FME : un interrupteur « Cache » à côté de « Lancer » remplace le réglage de chaque nœud
([ADR 0033](decisions/0033-mise-en-cache-globale.md)). Activé, « Lancer » ne recalcule que les nœuds modifiés et leur
aval ; les écritures et les sources externes (base, API web, STAC, SQL, flux lancés) sont toujours relancées, avec
leur aval. « Tout relancer » force un run complet. Désactivé : rien n'est gardé, l'inspecteur l'explique.

## Un lecteur et un écrivain par format (2026-10-13)

« Lecture de fichier » / « Écriture de fichier » remplacés par un modificateur par format, comme dans FME : CSV,
Excel, GeoPackage, Shapefile, GeoJSON, FlatGeobuf, GeoParquet, MapInfo ([ADR 0032](decisions/0032-un-lecteur-par-format.md)).
Chacun n'a que ses réglages, et « Parcourir… » ne montre que ses fichiers. Catégories « Lecture » et « Écriture »
(PostgreSQL compris). Les flux existants sont convertis à l'ouverture ; un chemin sans format déductible garde
l'ancien nœud, caché de la palette mais toujours exécuté. 102 modificateurs (16 de plus, 2 cachés).

## Retours d'usage : assistant, chargements, ports, nom du flux (2026-10-13)

- **Assistant** : les bulles se chevauchaient (grille dont les lignes étaient dimensionnées avant le retour à la ligne
  du texte) : colonne flexible. Les outils de lecture s'affichent en clair (« Lecture du flux », « Lecture d'un
  fichier » + cible), en cours (cercle) puis faits ; « Réflexion… » avec le temps écoulé tant que le modèle n'a rien
  dit, et après 20 s une explication (modèle en chargement ou fournisseur très sollicité). Événements `thinking` et
  `tool_start` ajoutés au flux SSE. Une proposition qui invente un paramètre (« relation » d'un ExtractByLocation) est
  renvoyée au modèle, qui corrige avant que l'utilisateur ne la voie ; un paramètre requis laissé vide reste permis.
- **Chargements** : composant `Spinner` / `Loading` partagé ; inspecteur (port, filtres, N, statistiques, tuiles de
  la carte), accueil, runs, explorateur de fichiers (partages lents), paramètres, versions de composants.
- **Comptes des ports** : affichés une seule fois, sur le port (l'étiquette au milieu des liaisons courtes tombait
  sur le port) ; le nœud s'élargit selon son contenu (190 à 300 px).
- **Nom du flux = nom du fichier** (mode fichiers) : « Nouveau flux » *non enregistré*, puis le nom du fichier choisi,
  non modifiable ; renommer = *Enregistrer sous…*. Le moteur l'impose (`filestore._named`) : runs, planifications et
  récents portent ce nom. Le mode base garde son champ de nom.
- **Sélection** : Ctrl+clic (ou Maj+clic) retire un nœud d'une sélection faite au cadre (le cadre de sélection de
  React Flow interceptait le clic).
- **CountPointsInPolygon** (« Compter les points dans les emprises ») : nombre de points de chaque polygone (0 compris)
  et sommes d'attributs ; points lus lot par lot en parallèle, seuls leurs comptes restent en mémoire.

## Carriole : nouveau nom et logo (2026-10-13)

L'application s'appelle **Carriole** (la charrette, « carry all », et i/o) : [ADR 0031](decisions/0031-nom-carriole.md).
Logo : une charrette chargée de données dont le montant, la béquille et la roue forment « i/o ». Les dossiers
`AtelierETL` sont renommés au premier lancement (rien n'est perdu) ; l'installeur remplace l'ancienne version. Le
dossier `desktop/build` (installeur, logo) est enfin versionné. Paquets Python, commande `etl` et variables `ETL_*`
inchangés.

**Python 3.14 sans GIL (essai)** : sur 500 000 parcelles, 8 threads, décodage et réencodage des géométries ×3,4,
reprojection ×2,6, boucles Python ×5 ; GEOS (tampon, surface, fusion) inchangé, il utilisait déjà tous les cœurs.
numpy, pyarrow, shapely, pyogrio, pyproj et rasterio fonctionnent sans GIL ; **DuckDB n'existe pas en version sans
GIL** (ni en 1.5.6 ni dans les versions de développement 1.6), psycopg-binary non plus : passage reporté.

## Jalon 18 (suite) : passe sur les modificateurs spatiaux (2026-10-12)

Même méthode que pour le Clip, sur tous les modificateurs spatiaux : banc sur le RPG (500 000 parcelles réelles,
leurs centroïdes et contours, les 586 communes de Haute-Garonne), recherche de la cause, correction. Règles communes
(guide [écrire un modificateur](ecrire_un_modificateur.md)) : aucune boucle Python par entité (`data.type_ids`,
`data.present`, fonctions Shapely sur tout le tableau) ; seules les géométries modifiées sont réencodées
(`data.replace_geometries`) ; tranches de 20 000 lignes (`Modifier.slice_rows`, `sdk.GEOMETRY_SLICE`) ; ce qui
ne change pas d'un lot à l'autre est mis en cache (`data.is_geographic`, transformations pyproj).

**Dissolve et mémoire** (retour de l'utilisateur : « out of memory, 6 Go pris » sur 1 472 471 parcelles regroupées
par `CODE_GROUP`, après le Clip). Cause : le modificateur décodait tous les ports en objets Shapely d'un coup
(~10 fois la taille du WKB). Maintenant : les géométries restent en WKB dans une colonne contiguë, le reste du port
est libéré (seule la première entité de chaque groupe est gardée), et chaque morceau de 10 000 est décodé au moment
de sa fusion. Sur le port réel : pic 4,4 → 3,0 Go (6,4 Go mesurés dans l'appli avant), 39 → 36 s, retombée à 1,2 Go
dès le chargement fini. Sans regroupement (un seul groupe de 1,47 M) : 4,0 Go, car le résultat fusionné est lui-même
gros. `sdk`/`data` : `zorder_bounds` (Morton depuis les emprises, sans garder les géométries décodées).
**Même faiblesse restante** : Aggregate (« collect ») et Hull par groupe décodent encore tout leur port ;
OverlayPolygons garde les polygones décodés d'un paquet.

**Compteurs de ports et de liaisons** : abrégés `950`, `12,3 k`, `1,6 M`, `2,1 Mds` (anglais : `B`), le nombre exact
en infobulle (`formatCount`).

**Mesures** (500 000 parcelles, 8 threads ; avant → après) :

| Modificateur | Avant | Après | Cause |
| --- | ---: | ---: | --- |
| Dissolve (23 groupes) | 314 s | 8,9 s | union de groupes dispersés : `disjoint_subset_union_all` (amas fusionnés à part) |
| MultipartToSingleparts | 41,5 s | 5,9 s | boucle par entité → `get_parts` |
| GeometryFromAttribute | 31,4 s | 14,0 s | lecture WKT entité par entité → vectorisée |
| CheckValidity | 21,6 s | 1,8 s | boucles, réencodage de toutes les géométries |
| PointFromXY | 20,7 s | 0,7 s | boucle par entité |
| Aggregate (collect) | 19,8 s | 7,0 s | boucle par géométrie → `get_parts` + `multipolygons(indices=…)` |
| GeometryToAttribute (WKT / GeoJSON) | 18,9 / 23,2 s | 10,6 / 15,6 s | conversion entité par entité |
| Centroids | 15,1 s | 2,5 s | test de présence et WKB entité par entité |
| ExtractCoordinates | 15,2 s | 2,6 s | idem ; premier/dernier sommet par géométrie |
| SplitByGeometryType | 15,2 s | 1,8 s | type entité par entité |
| JoinByNearest (k = 3) | 14,4 s | 4,9 s | lot unique : une seule tâche |
| MeasureArea / MeasureLength | 10,7 / 9,4 s | 2,2 / 1,4 s | type entité par entité, SCR relu à chaque lot |
| Hull (par entité) | 9,8 s | 4,8 s | un seul cœur |
| FixGeometries | 5,3 s | 2,0 s | réencodage de toutes les géométries |
| Buffer | 23,3 s, 3,9 Go | 17,4 s, 1,1 Go | lots de 100 000 en attente : tranches |
| Simplify | 9,5 s | 6,7 s | tranches |

Inchangés (déjà bons depuis le noyau spatial) : ExtractByLocation, JoinByLocation, PointsInPolygons, Clip
(2 à 5 s), OverlayPolygons, CreateGrid. ClipRasterByMask : les découpeurs ne sont plus reprojetés et fusionnés
pour chaque raster. Reproject (8 s) : la reprojection se parallélise, mais décoder et réencoder les géométries
garde le verrou de Python (limite de Shapely, voir l'ADR 0029). Tests : `engine/tests/test_spatial_pass_j18.py`.

**Essai SedonaDB / DataFusion (Rust), 2026-10-13** — mêmes données que le banc (RPG 1,6 M de parcelles, 586 communes) :

| Cas | SedonaDB | Notre moteur |
| --- | ---: | ---: |
| découpe 1,6 M × département | 267 s | 5 à 8 s |
| fusion 500 000 en 23 groupes | > 10 min (interrompu) | 9 s |
| jointure 500 000 × 586 communes | 0,4 s | 2 à 3 s |

SedonaDB ne gagne que sur la jointure (index spatial natif). Pour la découpe et la fusion il est bien plus lent :
le travail géométrique est fait par GEOS (la même bibliothèque que Shapely), sans géométrie préparée ni union par
sous-ensembles disjoints. Une réécriture en Rust ne changerait donc rien aux coûts dominants. Conclusion : pas de
réécriture ; la jointure par index de SedonaDB reste une piste pour les très gros volumes.

**Inspecteur et géométries énormes** — un Dissolve à 24 groupes avait produit un port de 600 Mo (une géométrie
de 176 Mo) écrit en un seul bloc Parquet : le tableau de l'inspecteur décodait tout pour afficher le type
(« failed to allocate 1 GiB »). Corrigé : les ports sont écrits en blocs de 128 Mo au plus (`row_groups`), le type
de géométrie du tableau est lu dans l'en-tête WKB sans décoder (`_wkb_kind`), et si une lecture dépasse encore la
mémoire de l'inspecteur le tableau s'affiche sans le type (la carte est alors indisponible). Tests dans
`api/tests/test_data.py`.

## Jalon 18 (suite) : inspecteur, N premières entités en tuiles vectorielles (2026-10-12)

Retour de l'utilisateur : 2 000 entités sur la carte, c'est trop peu ; revenir aux tuiles vectorielles, avec un N
réglable à la main qui vaut aussi pour le tableau. Décisions : [ADR 0030](decisions/0030-inspecteur-n-premieres-entites-et-tuiles.md).

**Livré**
- `etl_engine.view` : vue des N premières entités construite à la demande par l'API (une passe, Hilbert sur le monde,
  une vue par port, construction unique sous verrou) ; rien pendant le run.
- API : `n` sur chaque route de l'inspecteur (0 : toutes ; 100 000 par défaut) ; tuiles vectorielles de retour,
  géométries de tuile calculées par Shapely (vectorisé) et encodées par DuckDB ; `/features` (GeoJSON) retiré.
- Interface : champ « Chargées » (+ « Tout charger ») dans la barre de l'inspecteur, retenu ; carte en tuiles ;
  sélection toujours surlignée avec sa géométrie complète.
- Tests : `test_view.py`, `api/tests/test_data.py` (tuiles et N), scénario Playwright de l'inspecteur.

**Mesures** (RPG, 1,6 M de parcelles) : vue 3 s (100 000), 5 s (500 000), 16 s (tout) ; tuile de petite échelle
1,3 s au lieu de 24 s avec `ST_AsMVTGeom` ; page de table 0,3 à 1 s.

## Jalon 18 : noyau spatial (2026-10-12)

Essai réel : le Clip du RPG 2024 (1,6 M de parcelles) par la Haute-Garonne s'arrêtait après 157 s sur
« GEOS bad allocation ». Décisions : [ADR 0029](decisions/0029-noyau-spatial.md).

**Livré**
- `spatial.SpatialIndex` : test exact côté grande géométrie préparée (×200 sur le cas réel), copies préparées par
  thread (une géométrie préparée partagée plantait le processus), carreaux emboîtés pour les découpes locales
  (`local`), `overlay` et `union_groups` qui isolent les paires refusées par GEOS, `keep_dimension`.
- Clip réécrit : entités dedans ou dehors gardées telles quelles, seules les entités à cheval découpées, simples
  contacts laissés dehors, port « Rejected », tranches de 20 000 lignes.
- Profitent du noyau sans réécriture : ExtractByLocation, JoinByLocation, PointsInPolygons. Retouchés :
  CreateGrid, OverlayPolygons (contours des grands polygones coupés à l'emprise du paquet), JoinByNearest
  (k > 1 par tours vectorisés au lieu d'une boucle par entité).
- Erreur GEOS « bad allocation » → message de mémoire insuffisante.
- Tests : `engine/tests/test_spatial_j18.py`.

**Mesures** (cas réel) : Clip 7 à 8 s pour 1 601 817 parcelles, 1,5 Go (avant : arrêt après 157 s, 5,7 Go).

**Limite** : `shapely.from_wkb` garde le GIL, le gain en parallèle plafonne vers ×2 ; piste : Python sans GIL.

## Jalon 17 : performances, mémoire et runs parallèles (2026-10-12)

Retours après essai : le parallélisme ne se sentait pas, un traitement géospatial a fini en « out of memory », et il
faut pouvoir lancer plusieurs flux ensemble. Décisions : [ADR 0028](decisions/0028-performance-memoire-et-runs-paralleles.md).

**Causes trouvées** : un nœud attendait que son amont ait tout écrit en Parquet puis relu ; les vues d'inspection
(deux passes de reprojection et un tri complet par port) tournaient en parallèle du calcul avec une connexion DuckDB
sans limite ; chaque connexion DuckDB (une par lot en cours) s'autorisait 80 % de la RAM et tous les cœurs ;
les modificateurs spatiaux chargeaient tout, décodaient tout, et travaillaient sur un seul cœur ; rien n'arrêtait
proprement un run quand la mémoire manquait ; un poste mono-utilisateur était plafonné à 2 runs simultanés.

**Livré**
- *Pipeline* : un nœud à une entrée travaille en même temps que son amont (file bornée, ports toujours écrits).
- *Mémoire* : budget par run (mémoire, cœurs, débordement disque) pour toutes les connexions DuckDB ; nœuds
  « qui chargent tout » limités à 2 à la fois ; garde-fou qui arrête le run avec un message clair (nœud, Go) ;
  `ZonalStatistics` ne copie plus la table en Python ; écritures Parquet et CSV « tout ou rien ».
- *Modificateurs spatiaux* en flux et en parallèle : ExtractByLocation, PointsInPolygons, JoinByLocation, Clip,
  JoinByNearest (le côté « référence » est indexé une fois), OverlayPolygons par paquets de polygones voisins,
  Dissolve par union en arbre, ZonalStatistics par blocs de zones. Regroupements vectorisés (Aggregate,
  Hull, Dissolve), rang de JoinByNearest linéaire (il était quadratique).
- *Runs parallèles* : autant de flux simultanés que de workers (3 par défaut), chacun avec sa part de la machine.
- *Inspecteur* : plus de vue pendant le run ; la carte montre les N premières entités (2 000, 10 000 ou 50 000) de
  la table filtrée et triée ; la table reste paginée sur tout.

**Mesures** (1 M de points, 100 000 parcelles, 10 000 zones ; `bench/results_1000000_*.md`)

| Cas | Avant | Après |
| --- | ---: | ---: |
| chaîne de 5 nœuds | 16,3 s | 7,7 s |
| calcul + renommage | 7,3 s | 3,2 s |
| filtre | 6,4 s | 1,7 s |
| deux runs ensemble | 23,4 s | 11,7 s |
| point dans polygone | 13,0 s | 6,6 s |
| filtre spatial | 10,6 s | 5,9 s |
| fusion de polygones (sans groupe) | 12,5 s | 3,6 s |
| statistiques zonales | 38,5 s | 14,4 s |

**Limites** : le banc à 10 M n'a pas été rejoué (disque système presque plein) ; un nœud branché en direct ne
peut pas être ré-exécuté seul depuis le fichier de son amont sans lui (le port amont reste écrit, donc le cache
fonctionne comme avant) ; MIF s'écrit encore en une fois.

## Jalon 16 : produit propre (2026-10-12)

Retours après essai : le logiciel doit pouvoir être diffusé (ouvert ou vendu) et grossir en options.

- **Nettoyage** : plus aucune trace de l'ancienne version serveur. Supprimés : `etl migrate`, alias de chemins
  (`path_aliases`), fichiers témoins de partage, ADR 0006, 0012 et 0018 (0019 réécrite : écriture en deux temps) ;
  exemples de chemins (`D:/…`), commentaires et docs mis à jour. Les scénarios de bout en bout utilisent de
  vrais dossiers temporaires (`tests/paths.ts`).
- **Écriture en deux temps automatique** sur tout chemin UNC ou lecteur réseau mappé (elle n'était active que par
  variable d'environnement : sur le poste, un GeoPackage écrit sur `Z:` ne la recevait pas).
- **Barre du haut** : menus *Fichier*, *Édition*, *Exécution*, *Outils* (comme FME), actions courantes visibles
  (Enregistrer, Annuler/Rétablir, Lancer, Arrêter), *Runs*, *Assistant* et *Paramètres* à droite ; icônes ; composants
  communs à l'accueil et à l'éditeur (`AppBar`, `Menu`) ; la barre native d'Electron est masquée. Les champs
  « utilisateur » et « e-mail » quittent la barre pour *Paramètres*.
- **Paramètres** : fenêtre à onglets (Général, Calcul, Réseau, Composants, Variables globales, Assistant) sur une route
  `/api/settings` validée ; plus besoin de la ligne de commande. Nouveaux réglages : budget mémoire, proxy (exporté vers
  `HTTPS_PROXY` du moteur et de ses workers). Démarrage avec Windows, dossiers de données et redémarrage passent par
  l'IPC d'Electron. Les serveurs de tuiles s'appliquent immédiatement (en-têtes relus à chaque réponse).
- **Installeur** : assistant (langue, dossier) pour tous les utilisateurs, droits d'administrateur demandés au lancement
  (manifeste `requireAdministrator`), `C:\Program Files\Atelier ETL`, langue dans `HKLM`, `etl` dans le PATH de la
  machine ; bytecode livré (dossier en lecture seule) ; application empaquetée essayée depuis une copie en lecture seule.
  **Non essayé ici** : l'installation elle-même (session sans droits d'administrateur).
- **Reste** : essayer l'installeur sur un poste (administrateur), puis un autre compte Windows (langue reprise au
  premier lancement).

## Jalon 15 : assistant IA (2026-10-12)

Décisions : [ADR 0027](decisions/0027-assistant-ia.md).

- **Fournisseurs** : Anthropic (SDK officiel, streaming, prompt mis en cache), compatibles OpenAI (OpenAI, OpenRouter, Ollama),
  faux fournisseur scripté pour les tests. Liste des modèles lue chez le fournisseur. Clés chiffrées (coffre Windows), jamais
  renvoyées par l'API.
- **Outils** : 9 de lecture (catalogue, fichiers, flux ouvert, analyse, runs, échantillons) exécutés sans confirmation ;
  4 d'action (modifier le flux, lancer, écrire un modificateur, créer un composant) qui ne sont que des **propositions**
  appliquées d'un clic. Le modificateur écrit est testé dans un processus isolé avant installation.
- **Interface** : panneau Assistant (conversations, références `@` avec complétion, fichier joint, glisser-déposer dans
  l'application de bureau), cartes de proposition (diff, avertissements, appliquer / ignorer), réglages des fournisseurs.
- **Confidentialité** : « schéma seulement » par défaut pour un fournisseur distant, « schéma + N lignes » pour un modèle
  local ; mode visible en permanence dans le panneau.
- **CLI** : `etl ai "…" --flow f.json --out g.json`.
- **Tests** : 32 tests du paquet (outils, modifications, boucle d'agent, SDK Anthropic et OpenAI contre de faux serveurs
  HTTP, banc d'essai des plugins), 13 tests d'API, 2 tests de CLI, 3 scénarios Playwright (proposition appliquée puis
  annulée, modificateur installé, réglages). Aucun appel réseau en CI.
- **Reste à faire à la main** : un essai sur les vrais fournisseurs (clé Anthropic, OpenRouter, Ollama local) : la CI ne
  parle qu'à des serveurs simulés.

## Jalon 14 : rasters, STAC et inspecteur (2026-10-12)

Décisions : [ADR 0026](decisions/0026-rasters.md).

- **Modèle** : un port raster est une table dont chaque ligne est un raster (struct `raster`, emprise, attributs) ; les pixels
  restent dans des fichiers (GeoTIFF par tuiles, VRT), traités par fenêtres sur plusieurs fils, mémoire bornée.
- **17 modificateurs** : `RasterReader`, `RasterWriter`, `STACSearch`, `RasterCalculator` (numexpr), `WarpRaster`,
  `ResampleRaster`, `ClipRasterByMask`, `MergeRasters`, `TerrainAnalysis` (pente, exposition, ombrage), `ZonalStatistics`,
  `SampleRasterValues`, `Polygonize`, `Rasterize`, `RasterStatistics`, `StackBands`, `ReclassifyRaster`,
  `Contour`.
- **Inspecteur** : emprises sur la carte, image du raster en tuiles PNG (palette ou composition RVB, étendue réglable),
  valeur de chaque bande au clic.
- **Catalogue** : 68 → 85 modificateurs (l'objectif « 80 » du plan était une estimation), textes français et anglais.
- **Mesures** : NDVI sur 6000 x 6000 x 2 bandes, 4,4 s, +108 Mo de pic.
- **Tests** : 41 tests de rasters (dont un catalogue STAC simulé en HTTP, le flux Sentinel-2 NDVI par commune, le flux MNT
  de dalles ASC jusqu'à la pente, et la mémoire bornée), 5 tests de l'API d'inspection, 1 scénario Playwright.
- **Corrigé en route** : le catalogue STAC simulé pointait deux assets vers la même bande (NDVI nul) ; le schéma de sortie de
  `ZonalStatistics` annonçait `b1_*` alors que les colonnes portent le nom des bandes (schéma maintenant inconnu).

## Jalon 13 : 21 modificateurs tabulaires et vectoriels (2026-10-12)

Décisions : [ADR 0025](decisions/0025-ports-dynamiques-et-code-en-ligne.md).

- **Tabulaires (15)** : `RouteByExpression` (sorties nommées), `PivotWider`, `PivotLonger`, `CastAttributes`,
  `RenameAttributesByPattern`, `ReplaceText`, `ExtractText`, `ConvertDate`, `ExtractJson`, `BuildList`,
  `Sample`, `ExecuteSql` (SQL DuckDB sur plusieurs entrées), `CreateFeatures`, `HttpRequest` (parallèle, débit limité, reprises),
  `PythonScript` (fonction `process(table, ctx)`).
- **Vecteur (6)** : `OverlayPolygons`, `MeasureLength`, `MultipartToSingleparts`, `JoinByNearest` (k plus proches voisines
  exactes), `Hull` (convexe, concave, emprise, rectangle, cercle ; par entité, groupe ou tout),
  `CreateGrid` (carrés ou hexagones).
- **SDK et éditeur** : ports qui dépendent des paramètres (analyse et éditeur), widgets de code Python et SQL
  (CodeMirror 6 à la demande), listes à défaut non vide visibles dans les formulaires.
- **Catalogue** : 47 → 68 modificateurs, textes en français et en anglais, `etl i18n check` vert.
- **Tests** : 60 tests unitaires (pivots, conversions, JSON, HTTP local, SQL multi-entrées, géométrie), 1 scénario Playwright.

## Jalon 12 : variables, dossiers de fichiers, traitement par lots (2026-10-12)

Décisions : [ADR 0024](decisions/0024-variables-et-lots.md).

- **Variables** : bloc `variables` du flux (composables), `${sys:…}` (date, utilisateur, dossier du flux, run…),
  `${global:…}` (réglages), `${var:…}` posée par les données (`SetVariables`) et résolue au démarrage du nœud ;
  l'analyse refuse une variable que rien ne pose en amont.
- **5 modificateurs** (42 → 47) : `ListFiles`, `ReadListedFiles`, `SplitToFiles`, `RunFlow`, `SetVariables` ; le
  `Reader` accepte une liste de fichiers.
- **Lots** : `etl run --each / --each-list / --each-csv --as NOM --parallel N` (sous-processus, code de sortie) et
  « En lot… » dans l'application (un run par fichier d'un motif ou par valeur, regroupés dans la liste des runs).
- **Interface** : panneau *Variables du flux*, sélecteur `${…}` dans les champs, valeur résolue sous le champ.
- **Tests** : 24 pytest (variables, listage, lecture avec union de schémas, écriture par groupe, RunFlow, CLI),
  3 pytest d'API, 2 scénarios Playwright, vitest.

## Jalon 11 : internationalisation, français conservé et anglais complet (2026-10-11)

Décisions : [ADR 0023](decisions/0023-internationalisation.md).

- **Principe** : le français reste la langue du code et **le texte français est la clé** des catalogues (moteur,
  interface, application de bureau). Une langue de plus = un fichier JSON ; une clé absente s'affiche en français.
- **Moteur** (`etl_engine.i18n`, `locales/en.json`) : 467 textes de modificateurs (libellés, aides, paramètres,
  catégories) et 190 messages (erreurs de configuration, analyse, API, ligne de commande). `Modifier.describe(lang)`,
  `run_flow(lang=…)`, `analyze(…, lang)`. Les messages sont traduits à la frontière par gabarit (`{valeurs}` retraduites),
  sans toucher aux `raise` ; un run mémorise sa langue (`options.locale`).
- **API** : `Accept-Language` / `?lang=` sur toutes les routes ; erreurs traduites ; `GET/PUT /api/preferences` (langue
  du réglage, langues disponibles).
- **Interface** : i18next, 364 textes extraits (`frontend/src/locales/en.json`), dates et nombres selon la langue
  (`Intl`), boîte **Paramètres** (langue, effet immédiat, palette rechargée), couleur des catégories indépendante de la
  langue. Test : aucun texte accentué en dur, chaque `t("…")` traduit, mêmes `{{valeurs}}`.
- **Application de bureau** : menus, zone de notification et dialogues traduits (suivent le changement de langue de la
  page) ; **l'installeur propose la langue** (français / anglais), écrite dans le registre de l'utilisateur et reprise par
  l'application à son premier lancement, sans jamais écraser un choix fait ensuite dans les paramètres.
- **Outils** : `etl --lang en …`, `etl i18n check` (CI), `etl i18n extract <code>` (squelette d'une nouvelle langue),
  `etl modifiers doc --lang en` (`docs/en/catalogue.md`), plugins : `<plugin>.locales.json`.
- **Docs** : guide de l'utilisateur en anglais (`docs/en/user_guide.md`), catalogue en anglais.
- **Reste** : l'aide `etl --help` et les motifs de rejet écrits dans les données restent en français.

## Jalon 10 : fenêtre Electron et installeur sans droits administrateur (2026-10-11)

Décisions : [ADR 0022](decisions/0022-electron-et-installeur.md).

- **`desktop/`** (Electron, TypeScript) : fenêtre en bac à sable, dialogues natifs de fichiers (`etl:pick-file`) à la
  place de l'explorateur du serveur, menu, icône dans la zone de notification, instance unique, flux passé en argument,
  *Démarrer avec Windows*, arrêt du moteur à la sortie (avec choix s'il y a des runs en cours). Le moteur reste un
  processus détaché : les runs survivent à la fenêtre.
- **Python embarqué** (`scripts/build_desktop.ps1`) : Python géré par uv + dépendances du verrou + paquets de l'atelier,
  extension spatiale DuckDB et grilles PROJ téléchargées au build ; `etl_engine.install` retrouve ces ressources.
- **Installeur NSIS par utilisateur** : 224 Mo ; installation **sans élévation** vérifiée (session non administrateur) ;
  `etl` ajouté puis retiré du PATH de l'utilisateur (PATH restauré à l'identique après désinstallation) ; données
  conservées ; moteur arrêté avant mise à jour ou désinstallation.
- **Tests** : 2 scénarios Playwright `_electron` (fenêtre, dialogue natif d'enregistrement et d'ouverture, ouverture depuis
  le menu, fichier passé au démarrage, arrêt du moteur à la sortie) et 1 test de l'application **installée** (Python
  embarqué, flux avec `ST_Area` exécuté sans réseau).
- **Corrections en chemin** : un flux écrit à la main (sans `bookmarks`…) plantait l'éditeur (les fichiers sont maintenant
  complétés à la lecture) ; `etl run --submit` garde le cache par défaut (sinon l'inspecteur n'avait rien à montrer).
- **Reste** : pas de signature de code (SmartScreen avertit) ; textes du processus principal en français (jalon 11) ;
  mise à jour manuelle (réinstallation).

## Jalon 9 : application de bureau Windows (2026-10-11)

Point de départ : les utilisateurs travaillent sur le partage de l'équipe (`Z:`). Un programme Windows lit et écrit
ce partage avec les droits de l'utilisateur. Décisions : [ADR 0020](decisions/0020-application-de-bureau.md) (architecture, cible Linux/cloud) et
[ADR 0021](decisions/0021-flux-et-composants-en-fichiers.md) (flux et composants en fichiers).

- **Backend local** (`etl serve`) : API + workers supervisés dans un seul programme, port libre sur 127.0.0.1, jeton,
  contrôle de `Host`, en-têtes de sécurité (CSP, serveurs de tuiles configurables), un seul backend par
  utilisateur (verrou de fichier), arrêt propre par l'API (`etl stop`), workers pilotés par leur entrée standard.
- **Flux et composants en fichiers** : `FileStore`, récents, révision par empreinte, écriture atomique, versions de
  composants en fichiers voisins ; interface : *Ouvrir…*, *Enregistrer*, *Enregistrer sous…*, flux récents. Le stockage
  en base reste disponible (`ETL_FLOW_STORAGE=db`, mode serveur).
- **Chemins** : explorateur de repli listant le dossier personnel et les lecteurs.
- **Ligne de commande** : `serve`, `status`, `stop`, `open`, `inspect`, `runs`, `config`, `connections`,
  `run --submit/--wait/--log-file`.
- **Secrets** : clé de chiffrement protégée par DPAPI (une ancienne clé en clair est protégée au premier usage).
- **CI Windows** (`scripts/ci.ps1`) : ruff, plus de 440 tests pytest en natif (dont l'arrêt propre, le worker tué et la reprise
  du cache, la vraie ligne de commande), vitest, build, Playwright dans les deux modes (Edge) : 12 scénarios en base,
  2 en fichiers.
- **Performance** : banc à 1 M d'entités (filtre 3,2 s, calcul 7,0 s, reprojection 7,6 s, tri 4,6 s, GeoPackage
  14,6 s, CSV 5,8 s ; pic mémoire 406 à 815 Mo). Le banc à 10 M n'a pas été rejoué (disque système plein à 99 % pendant la séance : 4,7 Go libres).
- **Bogue corrigé en chemin** : `Store.try_acquire` plantait (`IntegrityError`) quand deux workers créaient le verrou
  de purge au même instant. Test de non-régression déterministe, validé par mutation.
- **Planification** : une planification dont le flux utilise un lecteur réseau mappé (`Z:`) affiche un avertissement
  (préférer `//serveur/partage`).
- **Reste** : un PostgreSQL de test pour le scénario « connexions » de Playwright (sauté sans `E2E_PG_HOST`).
- **Plan** : jalons 10 à 15 ajoutés ([plan](plan_jalons.md)). Pour atteindre **80 modificateurs** (42 aujourd'hui) :
  21 tabulaires et vecteur au jalon 13 et 17 rasters au jalon 14.

## Retours d'usage (2026-10-08)

Premiers retours après essai de l'atelier :

- **Fichiers du partage** : un **explorateur de fichiers** (bouton « Parcourir… », `GET /api/files`) ; en écriture,
  choix du dossier et du nom. Mêmes garde-fous que l'ADR 0016.
- **Page d'accueil** : liste des flux (description, auteur, nœuds, dernier run), recherche, composants à part,
  « Reprendre » l'éditeur ; l'adresse `#flow=…` ouvre toujours l'éditeur directement.
- **Aide à la saisie** : 93 paramètres sur 172 n'avaient pas de description ; tous en ont maintenant, avec le sens du
  champ vide (ex. *Requête SQL* vide = `SELECT *` sur la table) et un exemple affiché dans le champ vide.
- **Réparer les géométries** : nouveau modificateur `FixGeometries` (méthode, conservation du type, motif de
  réparation, rejet des géométries irréparables). 42 modificateurs.
- **Comptes sur les ports** : le nombre d'entités s'affiche à côté de chaque port du nœud, en direct.
- **Panneau du bas** : hauteur réglable à la souris (retenue) et pleine page, à la place du bouton d'agrandissement.
- **Partage lu et écrit** ([ADR 0019](decisions/0019-partage-reseau-lecture-ecriture.md)) : **écriture en deux temps**
  (local puis publication d'un bloc : rien d'à moitié écrit, pas de SQLite sur le partage). Essayée sur un partage SMB
  (Samba) : 12 contrôles sur 12.
- **Carte** : les couches de données passaient sous le fond de plan pour des points ou des lignes (le fond était
  inséré avant une couche `fill` qui n'existe que pour les polygones) ; corrigé et testé (ordre des couches).
- **Fond de plan** : l'absence d'OpenStreetMap venait de la CSP (`connect-src` / `img-src` sans les serveurs
  de tuiles) ; corrigé, avec un fond **satellite** (Esri) et la photographie aérienne de l'IGN, un test E2E qui
  vérifie que les requêtes partent.

**Limites connues** : le serveur de fichiers réel n'a pas été essayé (essais sur Samba), en particulier le
remplacement d'un fichier ouvert par un collègue ; les fonds de plan exigent un accès internet depuis les
navigateurs.

## Suites des questions ouvertes (2026-10-08)

- **Q1** : mots de passe `fme_decrypt` masqués dans les trois flux d'échantillon, désormais versionnés.
- **Q2** : pas de droits fins (décision).
- **Q3** : versions de composants publiées et épinglables dans le nœud ([ADR 0017](decisions/0017-versions-de-composants.md)).
- **Q4** : essai R-tree sur 10 M de points : 5 à 10 s par tuile à z7-z9, abandonné ([ADR 0010](decisions/0010-cache-geoparquet-vue-et-tuiles.md)).

## Jalon 8 — Compléments (2026-10-07)

**Livré**
- **Composants** ([ADR 0014](decisions/0014-composants.md)) : un flux déclare `component` ; ses nœuds Entrée/Sortie
  sont ses ports, ses paramètres publiés ses paramètres. Utilisable dans la palette comme un modificateur, remplacé
  par son contenu à l'analyse et à l'exécution, imbriquable, épinglé dans chaque run. L'éditeur ramène statuts,
  comptes, avertissements et erreurs du contenu au nœud composant ; un bouton ouvre sa définition.
- **Planification** ([ADR 0015](decisions/0015-planification.md)) : cron à cinq champs sans dépendance (heure d'été
  comprise), fil de planification dans chaque worker avec verrou en base, règles d'échéances manquées et de
  chevauchement, fenêtre « Planification » avec aperçu des prochaines échéances.
- **Durcissement** ([ADR 0016](decisions/0016-dossiers-autorises.md), [securite.md](securite.md)) : dossiers
  autorisés pour lire/écrire, en-têtes de sécurité (CSP…), corps de requête limités, revue consignée.
- **Documentation** : [guide de l'utilisateur](guide_utilisateur.md), README, catalogue généré, déploiement.

**Schéma des métadonnées** : v5 (connexions v3, composants v4, planifications v5) ; migrations rejouables.

**Limites connues**
- Pas d'authentification ni de droits fins (identité par en-tête, voir [securite.md](securite.md)).
- Un composant modifié change tous les flux qui l'utilisent à leur prochain lancement (pas de version explicite).
- Un seul fuseau pour toutes les planifications.

## Jalon 7 — Performance (2026-10-07)

**Livré** ([ADR 0013](decisions/0013-voies-execution-et-parallelisme.md))
- Lots traités en parallèle dans les nœuds « au fil de l'eau » sans état, résultats dans l'ordre d'arrivée.
- Voie SQL (DuckDB sur les fichiers Parquet des ports, débordement sur disque) pour OrderBy,
  StatisticsByGroup et JoinByField ; tests d'équivalence avec la voie Python.
- Budget mémoire : `ETL_MEMORY_BUDGET_MB` → `memory_limit` DuckDB (60 %) et dossier de débordement.
- Écriture GDAL en flux (GeoPackage ×4), Dissolve réparti par groupe.
- Corrigé au passage : « UnjoinedJoin » = entités de « join » sans aucune correspondance (comportement
  identique sur les deux voies).

**Mesures** (poste de dev, 12 cœurs, budget 3 Go ; `bench/results_*.md`)

| Cas | 1 M | 10 M | Objectif 10 M | Pic mémoire 10 M |
| --- | ---: | ---: | ---: | ---: |
| filtre | 3,0 s | 27 s | 90 s | 0,8 Go |
| calcul + renommage | 8,0 s | 69 s | 70 s | 0,8 Go |
| reprojection | 7,9 s | 71 s | 200 s | 1,1 Go |
| tri (2 clés) | 4,8 s | 50 s | - | 2,6 Go |
| statistiques par groupe | 5,3 s | 56 s | - | 1,9 Go |
| GeoPackage | 15 s | 243 s | 600 s | 1,0 Go |
| CSV | 6,2 s | 56 s | 300 s | 0,6 Go |

Tuiles à 10 M d'entités (vue d'inspection : 19 s) : médiane 435 ms à z7, 587 ms à z9, moins de 200 ms
au-delà ; table triée sur une colonne 0,7 s. Le seuil de l'ADR 0010 (500 ms) est dépassé de peu à z9 : la base
DuckDB avec index R-tree n'est **pas** construite, car le cache disque des tuiles rend les accès suivants
immédiats et 10 M de points inspectés restent un cas rare ; à rouvrir si les volumes inspectés grossissent.

**Limites**
- « calcul » à 10 M est juste sous l'objectif (69 s pour 70 s) : le temps part dans la lecture et l'écriture
  Parquet des ports plus que dans le calcul.
- Pas de fusion de nœuds contigus en une requête (l'inspecteur a besoin de chaque port).
- Les modificateurs spatiaux bloquants (Clip, ExtractByLocation…) chargent leurs entrées en mémoire.

## Jalon 6 — Catalogue de modificateurs (2026-10-07)

**Livré** : 39 modificateurs au total, décrits dans [catalogue.md](catalogue.md) (page générée par
`etl modifiers doc`, vérifiée par un test). Chacun a ses tests sur données synthétiques ; les flux
multi-entrées passent aussi par l'exécuteur (`test_catalogue_flow.py`).
- Lot A : StatisticsByGroup, JoinByField, LookupJoin, DropAttributes, Counter, OrderBy,
  FindDuplicates, Reproject, DatabaseQuery, DatabaseQueryPerFeature (PostgreSQL, paramètres liés).
- Lot B : ExtractByLocation, JoinByLocation, Aggregate, MapValues, ReplaceNulls,
  PointFromXY, ExplodeList, SplitText, MeasureArea, GeometryToAttribute, GeometryFromAttribute.
- Lot C : Buffer, Clip (union ou intersection avec attributs), Dissolve, PointsInPolygons,
  CheckValidity (avec réparation), ExtractCoordinates, Centroids, SplitByGeometryType, Simplify.

**Conventions** : un fichier par modificateur ; les entités qui ne peuvent pas être traitées partent sur
`Rejected` avec le motif ; les erreurs de configuration arrêtent le run ; schémas de sortie annoncés avant
exécution ; les deux entrées d'un modificateur spatial doivent avoir le même système de coordonnées (sinon
message qui renvoie vers Reproject).

**Limites / à rouvrir**
- Les modificateurs bloquants chargent leurs entrées en mémoire (voie plus économe au jalon 7).
- Pas d'équivalent « overlay » complet (OverlayPolygons) : Clip en mode « each » couvre
  l'intersection avec attributs.
- Écarts d'usage par rapport à FME : aucun import de flux n'étant prévu, ils ne sont pas suivis.

## Jalon 5 — Formats (2026-10-07)

**Livré** (détail et limites : [formats.md](formats.md))
- PostgreSQL/PostGIS : `PostgresReader` (table, vue ou requête, WHERE côté serveur, curseur serveur, lecture
  seule) et `PostgresWriter` (create, overwrite, append, truncate, upsert ; COPY ; transaction unique ; GIST).
- Connexions nommées : table `connections` (schéma v3), mot de passe chiffré Fernet, API
  (`/api/connections`, test, liste des tables), fenêtre « Connexions » et champ « Connexion » des modificateurs.
- Shapefile (renommages à 10 car. journalisés, `.cpg` UTF-8, garde-fou 2 Go), MapInfo TAB/MIF, Excel
  (feuilles et plage en lecture, feuilles multiples en écriture), GeoParquet partitionné + compression,
  Reader sur motif de fichiers avec colonne `filename` pour tous les formats.
- CI : base PostGIS de test (`ETL_TEST_PG`), E2E sur les connexions.

**Mesures** (1 M de points, 3 attributs) : écriture PostGIS 12 s, lecture 3 s.

**Décisions / limites**
- Un Reader = une couche : pas de ports de sortie dynamiques (un Reader par couche à lire). Choix volontaire,
  à rouvrir si l'usage l'exige.
- `numeric` lu en flottant ; MIF écrit en une fois (mémoire) ; précision TAB ~1 cm.
- Lecture/écriture PG convertissent via des objets Python (COPY texte) : correct mais pas optimal → jalon 7.
- La base de production n'a pas été sollicitée : les tests utilisent une base PostGIS jetable.

## Jalon 4 — Runs en arrière-plan (2026-10-07)

**Livré**
- File de jobs en base (schéma v2, migration) : réservation atomique, concurrence globale et par utilisateur,
  heartbeat, annulation, événements rejouables (SSE + `Last-Event-ID`).
- Reprise : `POST /runs/{id}/retry` (même flux figé, `resume` repart du cache). Worker arrêté (SIGTERM) ou tué
  (SIGKILL) → run « interrompu » (par le worker ou par le balayage de l'API), puis reprise.
- Rétention/purge du cache (âge, taille max, runs protégés), verrou périodique partagé entre workers.
- Notification de fin : toast + Notification navigateur, e-mail SMTP optionnel.
- Interface : fenêtre « Runs » (filtres, ouvrir, journal, données, relancer, reprendre, annuler).
- Test d'intégration sur un vrai backend (worker tué, interruption, reprise), appelé par la CI.

**Limites connues**
- Pas encore de « depuis un nœud » explicite : la reprise s'appuie sur les empreintes du cache.
- Les runs « interrompus » sont repris à la main (pas de reprise automatique).

## Jalon 3 — Inspecteur (2026-10-07)

**Livré**
- Vue d'inspection par port géométrique (tri Hilbert, `bbox`, `geom_3857`, `fid`), construite en tâche de fond
  par le moteur ; [ADR 0010](decisions/0010-cache-geoparquet-et-tuiles.md) mis à jour avec les mesures.
- Service de données (DuckDB verrouillé sur le cache) : pages de lignes, tri multi-colonnes, filtres par colonne
  (contient, égal, plage, liste, nul…), clause WHERE libre, recherche globale, total et total filtré, statistiques
  par colonne et validité des géométries, valeurs fréquentes, position d'une entité dans la liste triée, attributs
  d'une entité, export CSV / GeoPackage / Parquet de la vue filtrée, tuiles MVT filtrées avec cache disque.
- Interface : onglet « Données » (table virtualisée, pages chargées à la demande, filtres sous chaque en-tête,
  statistiques), carte MapLibre (style automatique, coloration par attribut, superposition de ports, info-bulle,
  fond de plan configurable ou hors ligne), sélection synchronisée, panneau agrandissable ; un clic sur une
  connexion ouvre ses données ; repère de cache sur les ports.
- Parcours Playwright : table, filtre, tri, carte (entités effectivement rendues), clause WHERE, message d'erreur.

**Mesures** (1 M d'entités) : vue en 3,2 s ; tuile à froid 33 à 220 ms ; page de table 80 à 100 ms.

**Constats**
- Le worker de MapLibre 6 doit être fourni à Vite explicitement (`?worker&url`), sinon « Worker failed to load » dès
  que le code est dans un chunk chargé à la demande. Sans WebGL, la carte est remplacée par un message.
- `1) OR (1=1` passait la vérification d'expression et pouvait contourner les autres filtres : contrôle de l'équilibre
  des parenthèses ajouté (moteur et inspecteur).

## Jalon 2 — Éditeur minimal (2026-10-07)

**Livré**
- Paquet `store` (SQLAlchemy, SQLite WAL) : flux (révision, conflit 409), runs, événements, file de jobs
  (réservation atomique, limites de concurrence par serveur et par utilisateur), heartbeat, annulation.
- API : `/api/modifiers` (palette + JSON Schema), CRUD `/api/flows`, `/api/analyze` (schémas par port et
  avertissements, à chaque édition), `/api/runs` (création → un worker exécute), journal, événements SSE
  avec reprise (`Last-Event-ID`). Identité légère par en-tête `X-ETL-User`.
- Worker : prend les jobs, tient le heartbeat, annulation, arrêt propre (run `interrompu`), runs sans heartbeat
  marqués `interrompu`.
- Éditeur React : canvas React Flow (glisser-déposer, ajout rapide en tapant, relié au nœud sélectionné),
  palette avec recherche, formulaires générés depuis le JSON Schema (widgets attribut, expression, SCR, tableaux),
  attributs proposés d'après le schéma amont, annuler/rétablir, copier-coller, désactivation, annotations,
  signets repliables, alignement, mini-carte, paramètres publiés, comptes en direct sur les connexions,
  statut/temps/mémoire par nœud, journal filtrable (clic → nœud), avertissements avant exécution,
  reconnexion au dernier run d'un flux, URL du flux (`#flow=…`).
- Tests : pytest (store, API, worker bout en bout), Vitest, Playwright (3 parcours) dans la CI locale.

**Constats**
- Les E2E ont révélé deux défauts réels : un sélecteur Zustand instable (écran blanc) et le placement des nœuds
  ajoutés au clavier. Ajout d'une boundary d'erreur React.
- Une partie du jalon 4 est déjà faite (file de jobs, reconnexion, annulation, interruption) ; reste liste des runs,
  notification/e-mail, purge du cache.

## Jalon 1 — Moteur, SDK et CLI (2026-10-07)

**Livré**
- Format de flux JSON (`flow.py`), paramètres publiés `${NOM}` et `${env:NOM}`.
- SDK : `Modifier` (ports, `Rejected`, schéma de sortie sans exécuter, streaming/bloquant), paramètres
  Pydantic avec widgets (`Attribute`, `Expression`, `Crs`, …), découverte par dossier et entry points.
  Guide : [ecrire_un_modificateur.md](ecrire_un_modificateur.md), exemple commenté, harnais `etl_engine.testing`.
- Exécuteur : ordre topologique, branches en parallèle, comptes en direct, annulation, délai maximal, mémoire
  par nœud, cache par port avec empreintes (réutilisation, « jusqu'à un nœud »), rejets ligne à ligne.
- Analyse sans exécution : schéma de chaque port, avertissements (attribut absent en amont, entrée non connectée, boucle).
- Formats : GeoParquet 1.1 (WKB, SCR, bbox, types), GeoPackage (plusieurs couches, ajout), CSV (séparateur et
  encodage détectés, WKT ou X/Y), plus Shapefile/MapInfo/GeoJSON/FlatGeobuf par GDAL.
- Modificateurs : Reader, Writer, AddAttributes, RefactorAttributes, ExtractByExpression, RenameAttributes, RetainAttributes.
- CLI `etl` : `run`, `validate`, `modificateurs list`, `new-modifier`.

**Benchmark 1 M d'entités** (poste de dev, détails dans `bench/results_1000000.md`)

| Cas | Temps | Pic mémoire |
| --- | ---: | ---: |
| filtre attributaire | 3,4 s | 338 Mo |
| attribut calculé + renommage | 7,4 s | 397 Mo |
| écriture CSV | 5,8 s | 401 Mo |
| écriture GeoPackage | 51,9 s | 400 Mo |

Objectifs tenus. Le GeoPackage est le goulot (écriture lot par lot) : à retravailler au jalon 7.

**Limites connues**
- Le cache n'est pas encore trié Hilbert ni enrichi de colonnes bbox/3857 : arrive avec l'inspecteur (jalon 3).
- Les rejets d'un RefactorAttributes gardent les colonnes du moment du rejet (schéma variable selon la règle fautive).

## Jalon 0 — Analyse (2026-10-07)

**Livré**
- Dépôt git, secrets hors du dépôt (`.env` non versionné).
- Analyse d'un échantillon de flux FME (non publié) : sert à ordonner le catalogue.
  Pas d'importeur FME (recadrage du 2026-10-07).
- 12 [ADR](decisions/), squelette `engine/ api/ worker/ frontend/`, CI locale.

**Constats**
- Top des modificateurs FME (nombre de flux) : AddAttributes, RefactorAttributes, RouteByExpression,
  StatisticsByGroup, RenameAttributes, RetainAttributes, ExtractByExpression, DatabaseQuery, Reproject, JoinByField.
