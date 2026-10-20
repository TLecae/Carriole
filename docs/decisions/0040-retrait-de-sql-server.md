# ADR 0040 — Retrait de SQL Server

- **Statut** : accepté
- **Date** : 2026-10-20
- **Modifie** : ADR 0036 (SQL Server, Oracle et SQLite)

## Contexte
SQL Server passait par `mssql-python`, qui embarque le pilote Microsoft ODBC Driver 18 (`mssql-python-odbc`, licence
propriétaire). Sa redistribution dans les installeurs est permise, mais à des conditions : faire accepter aux
utilisateurs des termes qui protègent Microsoft au moins autant que sa licence, et garantir Microsoft contre les
réclamations liées à l'application. Incompatible avec une diffusion libre et gratuite simple (ADR 0039).

## Décision
1. **SQL Server n'est plus pris en charge** : lecteur et écrivain (`SqlServerReader`, `SqlServerWriter`), type de
   connexion `sqlserver` (et authentification Windows, chiffrement), schéma d'URI `mssql://`, dépendance
   `mssql-python`, préchargement de `msvcp140.dll` qui ne servait qu'à son pilote.
2. Restent PostgreSQL / PostGIS, Oracle (`oracledb`, licence Apache / UPL) et SQLite / SpatiaLite. La mécanique
   commune `formats/dbapi.py` est gardée : un autre dialecte pourra s'y ajouter.
3. Le champ `options` des connexions est gardé (schéma de base v7) ; aucun type n'en a aujourd'hui.

## Conséquences
- Un flux qui contient un nœud SQL Server signale un modificateur inconnu ; une connexion `sqlserver` déjà enregistrée
  reste listée mais ne peut plus servir (« type de base non pris en charge »).
- 104 modificateurs. Piste pour revenir : un pilote libre (FreeTDS via `pymssql`, LGPL) ou un pilote ODBC installé par
  l'utilisateur.
