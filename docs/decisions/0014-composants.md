# ADR 0014 — Composants (custom modificateurs)

- **Statut** : accepté
- **Date** : 2026-10-07

## Contexte
Une équipe finit par recopier le même morceau de flux (nettoyage d'une table, filtre, enrichissement…) dans
plusieurs flux. Il faut pouvoir le **nommer, le paramétrer et le réutiliser** comme un modificateur, sans écrire de
Python.

## Décision
- Un **composant est un flux** qui déclare `component: {name, label, category, description}`. Pas de format
  séparé : on l'édite, on le teste (lancé seul) et on le versionne comme n'importe quel flux.
- Ses **ports** sont les nœuds `ComponentInput` / `ComponentOutput` (un par port, nommés) ; ses **paramètres** sont
  ses paramètres publiés. Un fichier d'exemple facultatif sur `ComponentInput` permet de le lancer seul.
- Dans un autre flux, un nœud `component:<nom>` est **remplacé par le contenu du composant** avant l'analyse et
  l'exécution (`etl_engine.components.expand_components`) : identifiants préfixés `<nœud>__<interne>`, liens de
  part et d'autre raccordés aux frontières, `${PARAM}` du composant substitués. Le reste du moteur (plan, cache,
  exécuteur, voie SQL) ne connaît pas les composants. Imbrication jusqu'à 8 niveaux, récursion refusée.
- Les ports du nœud composant sont **consultables comme ceux de n'importe quel nœud** : le moteur crée des liens
  physiques vers les fichiers du port interne qui les alimente (inspecteur, comptes sur les liens).
  Statuts, avertissements et erreurs des nœuds internes sont ramenés au nœud composant dans l'éditeur.
- Fournisseurs : base de métadonnées (un flux « composant » par nom unique, colonne `flows.component_name`),
  dossier `ETL_COMPONENTS_DIR` de fichiers JSON (ligne de commande, git).
- **Épinglage** : à la création d'un run (ou d'une échéance planifiée), la définition des composants utilisés est
  copiée dans `Flow.subflows`. Relancer un run, ou le reprendre depuis son cache, rejoue exactement la même chose
  même si le composant a changé depuis.
- Dans la palette, le composant apparaît avec sa catégorie ; un bouton ↗ ouvre le flux qui le définit.

## Conséquences
- Modifier un composant modifie les flux qui l'utilisent **à leur prochain lancement**, tant qu'aucune version n'est
  publiée. Les versions explicites et leur épinglage dans le nœud sont décrits dans l'[ADR 0017](0017-versions-de-composants.md).
- Supprimer un composant laisse les flux qui l'utilisent en erreur claire (« modificateur inconnu »).
- L'empreinte de cache des nœuds internes est celle des nœuds développés : changer un paramètre du composant
  n'invalide que ce qui en dépend.
