# ADR 0013 — Voies d'exécution et parallélisme

- **Statut** : accepté
- **Date** : 2026-10-07
- **Complète** : [ADR 0003](0003-moteur-arrow-geoparquet-duckdb.md)

## Contexte
Le moteur doit traiter des dizaines de millions d'entités sans tout charger en mémoire, et profiter de tous les
cœurs. Mesures initiales (jalon 1) : un lot après l'autre, un seul cœur par nœud ; écriture GeoPackage à
52 µs par entité.

## Décision
1. **Lots en parallèle (voie Python).** Un modificateur « au fil de l'eau » et sans état traite plusieurs lots à la
   fois (`ETL_BATCH_WORKERS`, par défaut la moitié des cœurs). Les résultats sont rendus **dans l'ordre
   d'arrivée** (sortie déterministe) et la fenêtre d'attente est bornée (mémoire bornée). Un modificateur qui
   garde un état entre lots (`stateful`), ou bloquant, reste séquentiel. Threads, pas processus : Shapely 2,
   DuckDB et Arrow libèrent le GIL.
2. **Voie SQL (DuckDB).** Un modificateur peut implémenter `sql()` : requêtes par port de sortie, exécutées par
   DuckDB directement sur les fichiers Parquet des ports (`etl_engine.sqlroute`). DuckDB parallélise,
   lit/écrit en flux et **déborde sur disque** au-delà de `memory_limit` (60 % de `ETL_MEMORY_BUDGET_MB`).
   Pris en charge : OrderBy, StatisticsByGroup, JoinByField. Si `sql()` renvoie `None` (paramètres non
   exprimables, ex. jointure en mode « erreur si plusieurs correspondances ») ou si une entrée est vide, c'est
   la voie Python. **Les deux voies donnent le même résultat** : `test_sql_route.py` compare colonnes, types,
   lignes, ordre et système de coordonnées.
3. **Pas de fusion de nœuds contigus en une requête.** Chaque nœud écrit son port : l'inspecteur (table et carte
   à chaque connexion) en dépend. Fusionner ferait perdre cette visibilité. *Complété par le point 6 (pipeline).*
4. **Écriture GDAL en flux.** Les formats GDAL reçoivent tous les lots dans un seul appel (`write_stream`) :
   GeoPackage ×4 (52 → 13 s pour 1 M d'entités).
5. **Blocage par groupe.** Le Dissolve répartit ses groupes entre threads (GEOS libère le GIL).

6. **Pipeline entre nœuds** (jalon 17, [ADR 0028](0028-performance-memoire-et-runs-paralleles.md)) : un nœud à une
   seule liaison entrante démarre en même temps que son amont et reçoit ses lots par une file bornée, au lieu
   d'attendre l'écriture complète puis de la relire. Les ports restent écrits (cache, inspecteur), sauf en run sans
   cache quand tous leurs consommateurs sont branchés en direct.

## Conséquences
- Les modificateurs au fil de l'eau écrits par les utilisateurs profitent du parallélisme sans rien faire, à
  condition de ne pas garder d'état dans `self` entre deux lots (sinon `stateful = True`).
- Sur un serveur aux ressources limitées (quotas CPU), `ETL_BATCH_WORKERS` et `ETL_DUCK_THREADS` fixent les threads.
- Place disque : le débordement DuckDB utilise le dossier `cache`.
