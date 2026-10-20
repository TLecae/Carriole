# ADR 0008 — SDK des modificateurs

- **Statut** : accepté
- **Date** : 2026-10-07

## Objectif
Un développeur Python ajoute un modificateur en écrivant **un seul fichier**, sans toucher au
moteur ni à l'interface ; un géomaticien le branche ensuite dans l'éditeur.

## Décision
- Une classe `Modifier` par fichier : métadonnées (nom, catégorie, description, aide),
  ports d'entrée/sortie (`Rejected` ajouté automatiquement), paramètres typés en Pydantic v2.
- Les paramètres sont exportés en JSON Schema ; les types métier (attribut, expression, SCR,
  chemin, connexion, paramètre publié, choix) portent une extension `x-etl-widget` que
  l'éditeur transforme en formulaire.
- Mode `streaming` (méthode `process(batch, ctx)`) ou `blocking` (`run(inputs, ctx)`).
  `output_schema(inputs)` calcule le schéma de sortie sans exécuter.
- Découverte : dossier `ETL_PLUGINS_DIR` (fichiers `.py`) et entry points `etl.modifiers`.
- Une entité en erreur part sur `Rejected` avec `_rejection_reason` ; seule une erreur de
  configuration arrête le run.
- Outillage : `etl new-modifier`, harnais pytest `etl_engine.testing`.
