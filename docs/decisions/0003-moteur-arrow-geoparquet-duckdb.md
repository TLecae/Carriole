# ADR 0003 — Moteur : Arrow, GeoParquet, DuckDB

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
- **En mémoire** : tables Arrow, géométries en WKB, opérations Shapely 2 vectorisées.
- **Entre nœuds** : un GeoParquet par port et par run (cache), lu par lots.
  Les nœuds ne se passent jamais le jeu complet en mémoire.
- **Deux voies** : un modificateur exprimable en SQL (filtres, jointures, agrégats, expressions)
  s'exécute dans DuckDB (parallélisé par DuckDB) ; sinon Python par lots (row groups).
- Un modificateur est **streaming** (entité par entité, parallélisable par lots) ou **bloquant**
  (tri, dissolve, agrégation : voit toutes les données).
- Expressions utilisateur : SQL DuckDB, repli Python.

## Conséquences
Mémoire bornée par la taille d'un lot ; le cache sert aussi l'inspecteur (ADR 0010).
