# ADR 0023 — Internationalisation : le français est la source, les autres langues sont des catalogues

- **Statut** : accepté
- **Date** : 2026-10-11
- **Complète** : [ADR 0020](0020-application-de-bureau.md), [ADR 0022](0022-electron-et-installeur.md)

## Contexte
L'atelier est écrit en français, par et pour une équipe francophone. Il faut une version anglaise complète et pouvoir
ajouter d'autres langues plus tard, sans traduire tout le code ni changer l'aspect du français.

## Décision
- **Le français reste la langue du code.** Libellés, aides, messages d'erreur, textes de l'interface sont écrits en
  français ; aucune clé abstraite à retenir. Une langue de plus = un fichier de catalogue, jamais une modification du code.
- **Le texte français est la clé** du catalogue, partout :
  - moteur : `engine/src/etl_engine/locales/<langue>.json`, section `modifiers` (clés stables
    `<Modificateur>.label`, `<Modificateur>.params.<champ>.title`, `category.<catégorie>`…) et section `messages`
    (le message français avec ses `{valeurs}` nommées) ;
  - interface : `frontend/src/locales/<langue>.json`, `t("Texte français {{valeur}}")` (i18next, sans catalogue `fr`) ;
  - application de bureau : `desktop/src/locales/<langue>.json` (menus, dialogues).
  Une clé absente s'affiche en français : une traduction manquante ne casse rien.
- **Messages du moteur traduits à la frontière**, pas à chaque `raise` : le code lève des messages français, formés
  comme avant. `i18n.tr(message, langue)` reconnaît le gabarit (`attribut absent en amont : {attr}`), traduit et
  retraduit les valeurs qu'il contient ; un message composé (`nœud : configuration : …`) est traduit morceau par
  morceau. Appliqué aux événements et à l'erreur d'un run (`run_flow(lang=…)`), aux avertissements d'analyse, aux
  réponses d'erreur de l'API et à la sortie de la ligne de commande.
- **Quelle langue ?** Une requête d'API : paramètre `lang`, sinon `Accept-Language` (l'interface l'envoie), sinon le
  réglage. Un run mémorise sa langue (`options.locale`) : le journal est stocké une fois, dans la langue de celui qui l'a
  lancé. Un run planifié ou lancé en ligne de commande suit le réglage (`ETL_LANG`, `--lang`, `etl config set language`).
- **Choix de la langue** : à l'installation (sélecteur de l'installeur, écrit dans le registre de l'utilisateur), reprise
  par l'application à son premier lancement dans `settings.json`, puis modifiable dans *Paramètres* (ou
  `etl config set language`). Sans choix, le français : la langue du navigateur ou de Windows ne décide pas.
- **Plugins** : `<plugin>.locales.json` à côté du `.py`, `{"en": {"modifiers": {…}, "messages": {…}}}`.
- **Garde-fous** : `etl i18n check --lang <code>` (CI) liste les textes sans traduction et les traductions devenues
  inutiles ; `etl i18n extract <code>` crée le squelette d'une nouvelle langue. Des tests vérifient que l'interface ne
  garde aucun texte accentué en dur, que chaque `t("…")` a sa traduction, que les `{valeurs}` sont conservées.

## Conséquences
- Ajouter ou modifier un texte français oblige à mettre à jour `en.json` (la CI échoue sinon) : c'est le prix de
  l'absence de clés symboliques.
- Les motifs de rejet écrits dans les données (`_rejection_reason`) restent en français : ce sont des données.
- L'aide des commandes `etl --help` reste en français (elle est évaluée avant le choix de la langue) ; les messages,
  eux, suivent la langue.
- La couleur d'une catégorie ne dépend pas de la langue (`category_id` : le français du code).
