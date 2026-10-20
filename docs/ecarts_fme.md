# Écarts de comportement avec FME

Les flux FME ne sont pas importés : on garde ici les comportements de FME dont on s'écarte
(ou dont on doute) quand un transformer Python imite un transformer FME. On ne devine pas :
chaque doute est noté avec l'hypothèse retenue.

| # | Transformer | Constat | Hypothèse retenue | Statut |
| --- | --- | --- | --- | --- |
| E1 | (décodage .fmw) | Le jeton `<bell>` apparaît dans 7 paramètres de l'échantillon, sa signification n'est pas documentée dans les fichiers | non décodé, laissé tel quel | sans objet (échantillon retiré) |
