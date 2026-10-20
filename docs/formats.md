# Formats pris en charge

| Format | Lecture | Écriture | Modificateur | Notes |
| --- | --- | --- | --- | --- |
| GeoParquet | oui | oui (partitionnée possible) | Reader / Writer | format natif du cache ; compression zstd, snappy, gzip |
| GeoPackage | oui (couche) | oui (plusieurs couches, ajout) | Reader / Writer | un Writer par couche dans le même fichier |
| Shapefile | oui | oui | Reader / Writer | voir limites ci-dessous |
| MapInfo TAB / MIF-MID | oui | oui | Reader / Writer | précision ~1 cm en Lambert-93 |
| GeoJSON, FlatGeobuf | oui | oui | Reader / Writer | |
| CSV | oui (WKT ou X/Y) | oui | Reader / Writer | colonnes lues en texte par défaut |
| Excel (.xlsx) | oui (feuille, plage) | oui (feuilles multiples) | Reader / Writer | géométrie écrite en WKT |
| PostgreSQL / PostGIS | oui (table, vue, requête) | oui (create, overwrite, append, truncate, upsert) | PostgresReader / PostgresWriter | connexions nommées |
| Oracle | oui (table, vue, requête) | oui (mêmes modes) | OracleReader / OracleWriter | connexions nommées ; SDO_GEOMETRY |
| SQLite / SpatiaLite | oui (table ou requête SQL) | oui (plusieurs tables, ajout) | SpatiaLiteReader / SpatiaLiteWriter | fichiers .sqlite, .db |

## Shapefile

- Les noms de colonnes sont limités à **10 caractères** : le Writer les raccourcit (suffixe `_1`, `_2`
  en cas de doublon) et **journalise chaque renommage**.
- Un fichier `.cpg` est écrit (UTF-8 par défaut) : les accents survivent à la relecture.
- Le `.shp` et le `.dbf` sont limités à **2 Go** : le Writer s'arrête avec un message explicite à 95 %
  de la limite (écrire alors en GeoPackage ou GeoParquet).
- Polygones simples et multiples sont acceptés dans la même couche (pas de distinction en Shapefile).

## MapInfo

TAB s'écrit par lots. MIF/MID ne s'ouvre pas en ajout : le Writer garde les lots en mémoire et écrit à la
fermeture (à réserver à des volumes raisonnables). Les coordonnées sont stockées en entiers dans les limites
de la table : précision de l'ordre du centimètre en Lambert-93.

## Excel

- Lecture : types natifs (entier, flottant, booléen, date) ; une colonne aux types mélangés est lue en
  texte. `Plage` (ex. `A2:F200`) : la première ligne de la plage est l'en-tête. Noms vides ou doublons
  renommés (`colonne_3`, `nom_1`). Les lignes entièrement vides sont ignorées.
- Écriture : le champ « Couche / feuille » nomme la feuille. `overwrite` ne remplace que cette feuille,
  `append` ajoute à la suite. Plusieurs Writer peuvent donc alimenter des feuilles différentes du même
  classeur (à enchaîner : ils écrivent le même fichier).
- Limite d'Excel : 1 048 576 lignes par feuille (erreur explicite au-delà).

## GeoParquet partitionné

« Partitionner par » écrit un sous-dossier `colonne=valeur/` par valeur (500 partitions maximum). La colonne
reste dans les fichiers. Pour relire : Reader avec le motif `dossier/**/*.parquet`.

## Oracle

Même fonctionnement que PostgreSQL (ci-dessous) : connexions nommées (type de base choisi dans *Connexions*), table ou
requête et filtre `WHERE` exécutés par le serveur, écriture en une seule transaction, modes create, overwrite,
append, truncate, upsert, SQL libre (*SQL (créateur)*, *SQL (par entité)*). Rien à installer sur le poste : le
pilote est livré avec Carriole (`oracledb` en mode « thin », sans client Oracle). SQL Server n'est plus pris en
charge ([ADR 0040](decisions/0040-retrait-de-sql-server.md)).

- **Oracle** : la connexion désigne un **service** (`hôte:1521/service`). Géométrie `SDO_GEOMETRY` (Oracle Spatial
  ou Locator) ; à la création d'une table, ses métadonnées (`USER_SDO_GEOM_METADATA`) et son index spatial sont
  créés. Les noms saisis sans guillemets sont en majuscules (`communes` → `COMMUNES`) ; les tables et colonnes
  créées aussi. Oracle valide aussitôt la création et la suppression d'une table : en mode *overwrite*, la table est
  recréée même si l'écriture échoue ensuite. Les entiers `NUMBER(p, 0)` sont lus en entiers, les autres `NUMBER` en
  flottants.
- **SQL libre en lecture seule** : sans « Autoriser l'écriture », l'ordre s'exécute dans une transaction **annulée**
  à la fin : rien n'est modifié.
- En ligne de commande : `ETL_CONN_<NOM>=oracle://utilisateur:mot_de_passe@hôte:1521/service`.

## SQLite / SpatiaLite

Bases fichiers (`.sqlite`, `.db`). La lecture prend une table **ou** une requête SQL (dialecte SQLite). Plusieurs
écritures peuvent remplir des tables différentes de la même base ; *overwrite* ne remplace que la table visée. Une
base créée par Carriole est une base **SpatiaLite** (métadonnées géométriques lues par QGIS ou FME). Pour un
GeoPackage (qui est aussi une base SQLite), utiliser les modificateurs GeoPackage.

## PostgreSQL / PostGIS

**Connexions.** Un flux ne contient que le **nom** de la connexion. Les connexions se gèrent dans
l'interface (bouton « Connexions » : créer, tester, supprimer). Le mot de passe est chiffré en base
(Fernet) et n'est jamais renvoyé par l'API ni écrit dans les journaux ou les flux
(voir [ADR 0011](decisions/0011-secrets-et-connexions.md)). En ligne de commande, sans serveur :
`ETL_CONN_<NOM>=postgresql://utilisateur:mot_de_passe@hôte:5432/base`.

**Lecture.** Table (`schema.table`) ou requête SQL, plus un filtre `WHERE` exécuté par le serveur.
Lecture seule (transaction `READ ONLY`), par curseur serveur : la mémoire reste bornée. La première colonne
géométrique devient `geometry` (WKB), son SRID le système de coordonnées ; les autres colonnes
géométriques sont lues en WKT. Correspondance des types :

| PostgreSQL | Arrow |
| --- | --- |
| smallint, integer, bigint | int16, int32, int64 |
| real, double precision | float32, float64 |
| numeric | float64 (perte de précision au-delà de 15 chiffres significatifs) |
| boolean | bool |
| text, varchar, char | string |
| date, timestamp, timestamptz, time | date32, timestamp(µs), timestamp(µs, UTC), time64 |
| json, jsonb, uuid | string |
| bytea | binary |
| tableaux (entiers, flottants, texte…) | list |
| autres (interval, hstore…) | string (représentation texte) |

**Écriture.** Une seule transaction : en cas d'erreur, la table reste comme avant (y compris pour
`truncate` et `overwrite`). Les données passent par `COPY`. Modes :

- `create` : échoue si la table existe ; `overwrite` : la recrée ; `append` : ajoute (table créée si
  absente) ; `truncate` : vide puis insère ; `upsert` : met à jour les lignes de même clé, insère les autres
  (le dernier doublon d'un lot l'emporte).
- À l'ajout dans une table existante, les colonnes des données doivent exister dans la table.
- Création : colonnes typées d'après le schéma Arrow, clé primaire si une clé est donnée, colonne
  géométrique `geometry(Type, SRID)` (nom réglable), index GIST, puis `ANALYZE`. En « auto », polygones et
  lignes sont créés en `Multi…` (les géométries simples sont promues) ; sinon le type du premier lot. Fixer le
  type explicitement si les données sont hétérogènes.

Mesuré sur le poste de dev (1 M de points, 3 attributs) : écriture 12 s, lecture 3 s.
