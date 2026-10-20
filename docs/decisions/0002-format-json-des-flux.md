# ADR 0002 — Format JSON natif des flux

- **Statut** : accepté
- **Date** : 2026-10-07

## Décision
Un flux est un fichier JSON lisible, stable et versionnable dans git (clés triées, indentation fixe) :

```json
{
  "schema_version": 1,
  "name": "…",
  "published_params": [{"name": "ANNEE", "type": "text", "default": "2025"}],
  "nodes": [{"id": "n1", "type": "Reader", "params": {}, "position": [0, 0], "enabled": true, "cache": null}],
  "links": [{"id": "l1", "from": ["n1", "output"], "to": ["n2", "input"], "enabled": true}],
  "annotations": [], "bookmarks": [], "subflows": {}
}
```

- `id` stables et courts (diffs git propres) ; ports nommés (`output`, `Rejected`, …).
- Un modèle Pydantic (`etl_engine.flow`) valide et exporte le JSON Schema.
- `subflows` est réservé dès le départ pour les custom modificateurs (jalon 8).
- Un flux exécuté est **figé** (copie dans le run) : relancer un vieux run rejoue le flux d'alors.

## Conséquences
Migration par `schema_version` ; l'éditeur ne stocke que la vue (positions, couleurs) en plus du graphe.
