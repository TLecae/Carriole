# ADR 0007 — Frontend

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
React 19 + TypeScript + Vite ; **@xyflow/react** (React Flow) pour le canvas ; **MapLibre GL**
pour la carte ; grille virtualisée (TanStack Table + Virtual) alimentée par le serveur ;
état Zustand ; données TanStack Query ; formulaires générés depuis le JSON Schema des
modificateurs (widgets maison pour attribut amont, expression, SCR). Tests : Vitest, Playwright.
Build statique servi par l'API locale (`etl serve`), qui expose aussi `/api` et `/tiles`.
Interface en français.
