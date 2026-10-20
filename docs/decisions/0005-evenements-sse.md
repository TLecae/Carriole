# ADR 0005 — Événements temps réel en SSE

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
Server-Sent Events (`GET /api/runs/{id}/events`) plutôt que WebSocket : flux unidirectionnel,
reconnexion native du navigateur, reprise par l'en-tête `Last-Event-ID` (= id de `run_events`),
traverse les proxys HTTP usuels sans configuration spéciale.

L'API relaie les lignes de `run_events` (sondage court côté serveur) ; elle ne garde aucun état :
redémarrer l'API ne touche pas aux runs.
