# ADR 0042 — Cache robuste, lecture par DuckDB, modificateurs fondus

- **Statut** : accepté
- **Date** : 2026-10-28
- **Complète** : ADR 0013 (voie SQL), 0033 (mise en cache globale), 0035 (cache gardé tant que le flux est ouvert),
  0038 (« jusqu'au nœud » reprend le cache), 0041 (modificateurs)

## Contexte
Retours d'usage :
1. Lancer « jusqu'au nœud » sur une branche (un nœud isolé) faisait perdre le cache des autres branches.
2. Pendant un run, les données d'un nœud déjà calculé n'étaient pas consultables.
3. Une écriture finie restait parfois « en cours ».
4. Lire un fichier de 20 M de lignes prenait de longues secondes, quand DuckDB seul le parcourt en une.
5. « Lecture GeoParquet » est aussi la seule façon de lire un Parquet sans géométrie : le nom trompait.
6. Plusieurs modificateurs faisaient la même chose sous des noms différents.

Causes trouvées :
1. et 2. Le moteur reprenait bien les nœuds hors de la branche (fichiers liés), mais annonçait leur état *cache*
   avant `run_started`, et l'interface remplaçait alors l'état de tous les nœuds. Les nœuds « toujours relancés »
   (requête web, flux lancé…) et leur aval n'étaient pas repris du tout, puis le run précédent était purgé. Un
   nœud repris n'était lié qu'à son tour dans le pool de threads, parfois occupé par des nœuds longs. Un nœud raster
   (sous-dossier `rasters/`) faisait échouer la reprise.
3. Le flux d'événements (SSE) pouvait voir le run fini sans avoir envoyé le dernier paquet d'événements (écrit
   juste avant), et une page rouverte ne relisait que 5 000 événements, sans consulter le résultat enregistré.
4. Chaque lecteur passait toutes les lignes par Python, lot par lot, puis pyarrow les réencodait dans le fichier du
   port, sur un seul thread, avec un dictionnaire sur toutes les colonnes (géométrie comprise). Le `count(*)`
   « instantané » de DuckDB ne lit que les métadonnées du fichier : la comparaison n'était qu'en partie juste.

## Décision
1. **Reprise du cache.**
   - `run_started` part avant tout état de nœud ; l'interface fusionne (les nœuds du run passent « en attente »,
     les autres gardent leur état).
   - Les nœuds repris passent au démarrage du run, avant tout calcul : consultables tout de suite.
   - Empreinte « statique » (réglages et amont, jamais salée) dans `meta.json` : un run « jusqu'au nœud » garde les
     derniers résultats des nœuds hors de sa branche dont rien n'a changé, même ceux qu'un run complet relance (le
     run complet suivant les relance toujours, ADR 0033 §3).
   - Reprise récursive (`rasters/`) ; les chemins des rasters (colonne `raster.uri`, sources des VRT) sont réécrits
     vers le nouveau run, dans de nouveaux fichiers (les liens vers l'ancien run restent intacts).
2. **États des nœuds.** Le flux SSE relit les événements après avoir vu le run fini, avant `end`. L'interface relit
   les événements par pages et applique le résultat enregistré du run (`result.nodes`) à la fin du run et à
   l'ouverture d'un run fini : il fait foi.
3. **Ports.** Dictionnaire Parquet seulement sur le texte ; encodage dans un thread propre au port (file de 2 lots) :
   le nœud prépare le lot suivant pendant l'encodage. Mêmes réglages pour l'écriture (Geo)Parquet, dont le suivi des
   types de géométrie et le partitionnement n'ont plus de boucle par entité.
4. **Lecture : la voie la plus rapide qui donne le même résultat, format par format.** La voie SQL (ADR 0013) vaut
   aussi pour les nœuds sans entrée : un lecteur qui sait s'exprimer en SQL écrit son port par
   `COPY (requête) TO port.parquet`, sur tous les cœurs, sans passer les lignes par Python. La lecture par lots
   reste la référence et le repli (`sql_fallback`) : si DuckDB échoue (type qu'il ne sait pas écrire, valeur
   illisible), le lecteur repasse par elle et donne son message habituel. Un run annulé interrompt la requête.
   Choix par format, mesurés (ci-dessous) :
   - **(Geo)Parquet** : `read_parquet` (fichiers locaux, motifs, adresses web), géométrie renommée selon les
     métadonnées « geo », nom du fichier, *Lignes lues au plus* (`LIMIT`) ;
   - **CSV** : `read_csv` avec les réglages actuels ; géométrie construite par DuckDB, WKT
     (`TRY(ST_GeomFromText)`) ou X/Y (`ST_Point`) ;
   - **GeoPackage, Shapefile, GeoJSON, FlatGeobuf, MapInfo, Excel** : lecture actuelle (pyogrio). `ST_Read` passe
     par le même GDAL, sur un seul thread, et s'est montré 1,5 à 3,5 fois plus lent ; `ST_ReadSHP` 7 fois ;
     `read_json` manque de mémoire sur un GeoJSON (un seul objet JSON de 221 Mo). Leur gain vient du point 3 et du
     schéma mémorisé (point 7).

   L'extension spatiale n'est chargée que pour construire une géométrie : son premier chargement dans un processus
   coûte 2,5 s, plus que la lecture d'un million de lignes.
5. **Nom « (Geo)Parquet »** pour le lecteur et l'écriture ; noms techniques inchangés (`GeoParquetReader`).
6. **Modificateurs fondus**, migrés à l'ouverture (`renames.py`) :
   - `LookupJoin` → `JoinByField`, qui gagne *Géométrie du résultat* (base / jointe / aucune ; la géométrie jointe
     garde son système). Différence : « UnjoinedJoin » ne reçoit que les entités jamais appariées.
   - `CountPointsInPolygon` → `JoinByLocation`, qui gagne *Attributs à additionner* et *first*. Sans liste, le côté
     gardé en mémoire et indexé est le plus petit des deux (connu par les fichiers des ports) : compter des millions
     de points dans des polygones reste peu gourmand.
   - L'option *Réparer* de `CheckValidity` disparaît : un ancien nœud réglé ainsi devient `FixGeometries`
     (linework, sans garder le type), ports `Valid` → `output` et `Invalid` → `Rejected`.

   Gardés : *Créer / Renommer / Retirer des attributs* à côté de *Gérer les attributs*, *Agréger* à côté de
   *Dissoudre* et des *Statistiques par groupe*, *Filtrer par expression* à côté du *Filtre à plusieurs sorties* :
   même famille, mais une ergonomie propre au cas courant.

7. **Schéma des fichiers mémorisé** par fichier (date, taille) et réglages : l'éditeur le redemande à chaque
   modification du flux, et GDAL relit tout un GeoJSON pour le donner (8 s pour un million de points).
8. **Plugins** : sans dossier de plugins, le chemin vide désignait le dossier courant, dont chaque script Python était
   exécuté au démarrage. Plus rien n'est chargé.

## Mesures
Banc `bench/run.py N lecture_<format>` : un lecteur seul, cache activé (le port est écrit, comme dans
l'application), points à 6 colonnes, poste de développement (Windows, 12 cœurs logiques, 16 Go). Fichiers de résultats
`bench/results_*_lecture*.md`.

| Lecture | Avant (s) | Après (s) |
| --- | ---: | ---: |
| (Geo)Parquet, 1 M | 1,3 | 0,5 |
| (Geo)Parquet, 10 M | 11,6 | 3,9 à 6,4 |
| CSV (WKT), 1 M | 7,0 | 1,3 |
| CSV (WKT), 10 M | 66,8 | 7,0 à 10,2 |
| GeoPackage, 1 M | 1,2 | 0,7 à 1,0 |
| Shapefile, 1 M | 13,1 | 11,7 à 12,7 |
| GeoJSON, 1 M (premier run) | 24,9 | 24,4 à 24,8 |
| FlatGeobuf, 1 M | 7,9 | 6,9 à 7,8 |

Écart entre deux mesures : l'état du disque (antivirus, cache du système). Pour les formats GDAL, la lecture
elle-même domine ; un GeoJSON relu dans le même processus ne relit plus son schéma (25 s → 18 s).

## Conséquences
- Un texte encodé par dictionnaire dans un Parquet lu par DuckDB devient un texte simple (mêmes valeurs).
- Un lecteur par DuckDB n'est pas branché en direct sur ses consommateurs (ADR 0013) : ils lisent son port une fois
  écrit, ce qui reste bien plus rapide que l'ancienne lecture en flux.
- CSV, colonnes X/Y : une coordonnée absente donne une entité sans géométrie (avant : un point vide).
