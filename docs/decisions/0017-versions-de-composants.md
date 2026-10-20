# ADR 0017 — Versions de composants

- **Statut** : accepté
- **Date** : 2026-10-08
- **Complète** : [ADR 0014](0014-composants.md)

## Contexte
Modifier un composant modifiait tous les flux qui l'utilisent à leur prochain lancement. Pour une équipe qui
partage des composants, il faut pouvoir **faire évoluer un composant sans casser** les flux qui en dépendent, et
choisir quand ils adoptent la nouvelle version.

## Décision
- Une **version** est un instantané immuable du composant, **publié explicitement** (bouton « Publier une version »
  dans le panneau du flux, avec une note). Table `component_versions` (nom, numéro 1, 2, 3…, flux, note, auteur,
  date). Rien n'est publié par un simple enregistrement : le flux enregistré reste le **brouillon**.
- Publier refuse (409) si rien n'a changé depuis la dernière version. L'instantané embarque les composants
  imbriqués, épinglés à leur version du moment (`embed_components`) : une version se rejoue à l'identique.
- Un nœud composant porte `component_version` : un numéro (épinglé) ou `null` (suit la dernière version publiée).
  Ajouté depuis la palette, un nœud est **épinglé à la dernière version** ; le panneau du nœud propose de changer de
  version et signale qu'une plus récente existe.
- **Résolution** (`etl_engine.components.resolve`, fournisseurs `(nom, version) -> (flux, version)`) : version
  demandée → sinon dernière publiée → sinon brouillon (aucune version publiée : comportement de l'ADR 0014, les
  composants existants continuent de fonctionner). Une version inexistante n'est pas remplacée : « modificateur
  inconnu ».
- **Épinglage dans le run** : `Flow.subflows` est indexé par `nom@N` (`nom` pour un brouillon) et les nœuds du flux
  embarqué reçoivent le numéro résolu. Relancer ou reprendre un run rejoue la même chose, même si le composant a
  évolué ou si ses versions sont supprimées. Idem pour une échéance planifiée : elle suit la dernière version (ou
  l'épingle choisie) au moment où elle crée le run.
- La palette décrit la **dernière version publiée** (ports, paramètres) ; les dossiers `ETL_COMPONENTS_DIR`
  (fichiers JSON, git) n'ont pas de versions : le fichier est la version.
- API : `GET/POST /api/components/{nom}/versions` (liste et `draft_changed`, publication).

## Conséquences
- Les versions restent en base si le flux composant est supprimé : les flux qui les épinglent continuent de
  tourner. Le composant disparaît seulement de la palette.
- Le brouillon n'est pas utilisable depuis un autre flux dès qu'une version existe : pour tester une évolution
  dans un flux, la publier (ou lancer le composant seul avec son fichier d'exemple).
- Pas de suppression ni de dépréciation de versions pour l'instant.
