# ADR 0036 — SQL Server, Oracle et SQLite / SpatiaLite

- **Statut** : accepté ; SQL Server retiré par l'[ADR 0040](0040-retrait-de-sql-server.md)
- **Date** : 2026-10-13
- **Complète** : ADR 0011 (secrets et connexions), ADR 0032 (un lecteur par format)

## Contexte
Seul PostgreSQL / PostGIS était pris en charge. L'utilisateur demande les autres bases : SQL Server, Oracle et
SQLite / SpatiaLite, en lecture, en écriture et en SQL libre. Le public n'est pas technique : rien ne doit s'installer
à côté de Carriole.

## Décision
1. **Pilotes livrés avec Carriole**, en Python :
   - SQL Server : `mssql-python` (Microsoft), qui embarque le pilote ODBC 18 (environ 45 Mo) ; authentification SQL
     ou Windows ;
   - Oracle : `oracledb` en mode « thin », sans client Oracle.
2. **Connexions typées** : `kind` (`postgres`, `sqlserver`, `oracle`) et `options` (JSON, schéma de base v7 : pour
   SQL Server, authentification, chiffrement et confiance dans le certificat). Mot de passe chiffré comme avant. URI
   pour la ligne de commande : `mssql://`, `oracle://`.
3. **Un lecteur et un écrivain par base**, comme pour les fichiers : SqlServerReader / SqlServerWriter,
   OracleReader / OracleWriter. Leur champ de connexion ne propose que les connexions du bon type (`x-etl-kinds`).
   DatabaseQuery et DatabaseQueryPerFeature acceptent toutes les bases ; le SQL est celui de la base choisie.
4. **Une mécanique commune** (`formats/dbapi.py`) pour SQL Server et Oracle ; chaque base n'apporte qu'un
   *dialecte* (`mssql.py`, `oracle.py`) : connexion, noms, types, géométrie, catalogue. PostgreSQL garde `pg.py`
   (COPY, curseur serveur), derrière la même interface (`formats/databases.py`).
   - Lecture en Arrow par le pilote (`arrow_batch`, `fetch_df_batches`) ; géométrie en WKB (`STAsBinary`,
     `SDO_UTIL.TO_WKBGEOMETRY`), SRID du flux d'après la colonne.
   - Écriture en une transaction, mêmes modes que PostgreSQL. SQL Server : *bulk copy* Arrow dans une table
     temporaire globale validée à part, recopiée dans la cible au sein de la transaction (le *bulk copy* ne peut pas
     viser une table créée par une transaction ouverte). Mesure : 200 000 points en 9,8 s, contre 3 500 lignes par
     seconde en INSERT. Oracle : `executemany`, 100 000 lignes en 1 s.
   - SQL libre « en lecture seule » : transaction annulée à la fin. Oracle n'utilise pas `SET TRANSACTION READ ONLY`,
     qui refuse de lire une table créée à l'instant (ORA-01466).
5. **SQLite / SpatiaLite** : un format de fichier (SpatiaLiteReader / SpatiaLiteWriter), lu et écrit par GDAL, qui
   gère SpatiaLite sans libspatialite ; table ou requête SQL (dialecte SQLite) ; plusieurs tables par base. Pas de
   connexion : c'est un fichier, comme le GeoPackage.
6. **`msvcp140.dll`** : le moteur charge d'abord celui, récent, du pilote SQL Server. Sur un poste dont le
   `msvcp140.dll` système est ancien (14.13), numexpr le chargeait, et le pilote SQL Server plantait le processus en
   se chargeant ensuite (violation d'accès).

## Conséquences
- Testé sur SQL Server 2022 et Oracle 23ai (conteneurs) : lecture, écriture, modes, upsert, SQL libre, SQL par
  entité. Tests d'intégration activés par `ETL_TEST_MSSQL` / `ETL_TEST_ORACLE`.
- **Oracle Spatial n'a pas pu être essayé ici** : l'image de test « slim » n'en a pas, l'image complète ne tenait pas
  sur le disque. Lecture et écriture de géométries Oracle à valider sur une vraie base, ce que couvre le test
  `test_oracle_geometry_roundtrip` (sauté sans Oracle Spatial).
- Installateur : environ 50 Mo de plus.
