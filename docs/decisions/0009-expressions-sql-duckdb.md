# ADR 0009 — Expressions en SQL DuckDB

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
Attributs calculés et conditions s'écrivent en **expressions SQL DuckDB** (`"surface" * 2`,
`CASE WHEN … END`, `ST_Area(geom)`), évaluées par DuckDB sur le lot Arrow. Alternative
Python (`lambda row`/fonction vectorisée) pour les cas qui ne s'y prêtent pas.

Sécurité : une expression est validée comme **expression unique** (pas d'instruction),
évaluée sur une connexion DuckDB sans accès fichiers/réseau.
