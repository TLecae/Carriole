# ADR 0001 — Monorepo uv workspace

- **Statut** : accepté
- **Date** : 2026-10-07

## Contexte
Interface, API, moteur et workers évoluent ensemble et partagent le moteur.

## Décision
Un dépôt, un workspace **uv** : `engine/` (moteur + SDK + CLI), `api/`, `worker/`, `frontend/` (npm, hors workspace Python). Python 3.12.
Un seul `uv.lock` ; le build de l'installeur installe exactement ces versions (`uv export --frozen`).

## Conséquences
Versions reproductibles ; `api` et `worker` dépendent de `engine`, jamais l'inverse
(le moteur reste exécutable sans l'API).
