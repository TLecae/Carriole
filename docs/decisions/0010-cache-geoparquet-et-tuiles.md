# ADR 0010 — Cache GeoParquet, vue d'inspection et tuiles

- **Statut** : accepté pour 1 million d'entités (mesuré) ; **la vue d'inspection et les tuiles vectorielles sont construites à la demande, pour les N premières entités du port ([ADR 0030](0030-inspecteur-n-premieres-entites-et-tuiles.md))**
- **Date** : 2026-10-07

## Décision
**Pipeline** : un GeoParquet par port, `cache/<run>/<nœud>/<port>.parquet`. Il n'est jamais réordonné : certains
modificateurs dépendent de l'ordre des entités. Empreinte du nœud = hash (type, version, paramètres, empreintes
amont) → réutilisation d'un run à l'autre.

**Vue d'inspection** : pour chaque port géométrique, une copie `<port>.view.parquet` écrite à la fin du nœud, en tâche
de fond (`etl_engine.view`) :

- triée selon la **courbe de Hilbert** (en EPSG:3857) ;
- colonne `bbox` (emprise de chaque entité) : le filtre spatial passe par les statistiques de row groups ;
- colonne `geom_3857` : la **reprojection est faite une seule fois**, à l'écriture ;
- colonne `fid` : numéro de ligne d'origine, identifiant commun de la table et de la carte ;
- row groups de 100 000 lignes, compression zstd ; un fichier `.view.json` décrit SCR, types de géométrie, emprise.

Les ports sans géométrie, ou sans SCR connu, n'ont pas de vue : la table reste disponible, pas la carte.

**Tuiles** : MVT générées à la demande par DuckDB spatial (`ST_AsMVT`/`ST_AsMVTGeom`) sur la vue, avec :
filtre d'emprise sur `bbox`, plafond de 20 000 entités par tuile (échantillonnage par `hash(fid)` au-delà),
simplification des lignes et polygones selon le zoom, filtre de la table transmis dans l'URL, cache disque par
run/nœud/port/filtre et en-têtes HTTP `immutable`.

**Sécurité** : la connexion DuckDB de l'inspecteur ne lit que le dossier du cache (`allowed_directories`), sans accès
réseau, configuration verrouillée, délai maximal par requête ; la clause WHERE libre doit être une expression unique
aux parenthèses équilibrées.

## Mesures (1 000 000 de points, poste de dev, tuiles à froid — `bench/tiles.py`)

| | Résultat |
| --- | ---: |
| Construction de la vue | 3,2 s |
| Tuile z5 / z7 / z9 / z11 / z13 / z15 (médiane) | 33 / 220 / 213 / 94 / 72 / 74 ms |
| Pire tuile mesurée (incluant l'ouverture de la connexion) | 838 ms |
| Tuile z9 avec filtre WHERE | 100 ms |
| Table : tri sur une colonne, page de 100 | 104 ms |
| Table : filtre de colonne + compte filtré | 79 ms |

## Alternative écartée (pour l'instant)
Matérialiser le jeu inspecté dans une base DuckDB avec index R-tree : inutile tant que le Parquet trié répond en
moins de ~300 ms à 1 M d'entités. À reconsidérer si la mesure à 10 M (jalon 7) dépasse 500 ms par tuile.

## Addendum (jalon 7) — mesure à 10 M d'entités
Vue d'inspection construite en 19 s ; tuiles à froid : médiane 435 ms (z7) et 587 ms (z9), moins de 200 ms à
partir de z11 ; `bench/results_tiles_10000000.md`. Le seuil de 500 ms est dépassé de peu à z9 : décision de ne pas
matérialiser de base DuckDB avec R-tree pour l'instant (cache disque des tuiles, cas rare), à rouvrir au-delà.

## Addendum (2026-10-08) — R-tree abandonné
Essai d'une base DuckDB avec index R-tree sur 10 M de points : 5 à 10 s par tuile à z7-z9 (bien au-delà du Parquet
trié, 0,6 s), car ces tuiles contiennent des centaines de milliers d'entités. Abandonné sur décision de l'équipe ;
le seuil de 500 ms reste dépassé de peu à z9 sur 10 M, sans effet sur les volumes courants.
