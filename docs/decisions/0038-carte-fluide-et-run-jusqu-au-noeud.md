# ADR 0038 — Carte fluide ; « jusqu'au nœud » reprend le cache ; bandeau du nœud

- **Statut** : accepté
- **Date** : 2026-10-14
- **Modifie** : ADR 0037 point 3 (les plus grandes entités de la vue), ADR 0033 point 3 (sources toujours relancées)

## Contexte
1. La carte manquait de fluidité : le choix des « plus grandes entités de la vue » changeait l'adresse des tuiles à
   chaque zoom (toutes rechargées, carte vide le temps du calcul : 0,3 à 1,4 s par tuile aux petites échelles) ; un
   changement de filtre ou de couleur effaçait la carte.
2. « Jusqu'au nœud » recalculait tout : les lectures de base étaient toujours relancées (et leur aval avec elles), et
   seul le run précédent était repris (un nœud hors de la branche du run précédent était perdu pour le suivant).
3. Les actions sur un nœud étaient cachées dans les menus ; un clic sur un nœud ne montrait pas toujours ses
   données ; le contour de sélection disparaissait sous la bordure verte d'un nœud terminé.

## Décision
1. **Tuiles indépendantes de la vue.** Le choix des entités se fait par **métatuile** (4 × 4 tuiles du même
   zoom) : au plus `MAP_LIMIT` (50 000), les plus grandes classes de taille ; points échantillonnés. Compté sur
   l'index des entités, désormais trié selon Hilbert (vue version 4 : 0,02 à 0,2 s par zone), gardé en mémoire et
   sur disque. L'adresse d'une tuile ne dépend que du filtre et de la couleur : le navigateur et le disque la
   gardent. Plus de message « … les plus petites ne sont pas dessinées » ni de route `in-view` : la carte
   montre seulement qu'elle charge.
2. **Tuiles des petites échelles calculées d'avance**, en arrière-plan, après la vue : zooms 0 à 10, au plus 2 000
   tuiles par port. Mesure sur 1,6 million de parcelles : 247 tuiles en 35 s (2 tâches), 17 Mo ; une tuile prête
   est servie en 7 ms. Tuiles gardées et envoyées **compressées** (gzip).
3. **Interface** : la source de tuiles est créée une fois par port ; filtre et couleur changent son adresse
   (`setTiles`) et la couleur des couches : l'ancienne image reste jusqu'à la nouvelle. Pas de fondu à l'arrivée
   d'une tuile.
4. **Pas de seconde adresse pour les tuiles** (12 requêtes simultanées au lieu de 6) : le jeton de session est un
   cookie lié à l'adresse ; il faudrait le mettre dans l'URL des tuiles. Les tuiles précalculées rendent la limite
   du navigateur secondaire.
5. **« Jusqu'au nœud » reprend le cache.** `Modifier.reusable = "partial"` (lectures de base, DatabaseQuery, STAC) :
   relues par *Lancer*, reprises par *Jusqu'au nœud* si leurs réglages n'ont pas changé (leur empreinte salée du run
   précédent est gardée : leur aval reste repris). Écritures, DatabaseQueryPerFeature, appels HTTP et flux lancés restent
   toujours relancés. Un run « jusqu'au nœud » **reprend aussi les nœuds hors de sa branche** encore valables
   (fichiers liés, état *cache*) : le run suivant les retrouve, et l'inspecteur les montre.
6. **Bandeau du nœud** sélectionné (seul) : lancer jusqu'à lui, désactiver / réactiver (icônes et bulles d'aide).
   **Clic sur un nœud** : ses données dans l'inspecteur, onglet *Données* affiché, à chaque clic (même déjà sélectionné), ou un
   message s'il n'en a pas dans ce run (au lieu de laisser celles du nœud précédent). Contour de sélection marqué,
   placé après les états.

7. **Le port consulté passe d'abord.** Les tuiles attendent une place de calcul (`_Gate`) : celles que la carte
   demande passent avant celles calculées d'avance ; une tuile dont le navigateur a annulé la requête (autre port,
   carte fermée) quitte la file sans calcul. Seul le dernier port ouvert calcule ses tuiles d'avance (celui d'avant
   s'arrête, et reprend à sa prochaine ouverture) ; une vue cartographique demandée passe devant celles qui
   attendent encore leur tour (celles commencées vont au bout).

## Conséquences
- À petite échelle, une zone dense montre ses plus grandes entités même si l'écran n'en contient pas 50 000 (la
  zone de 4 × 4 tuiles est plus grande que l'écran) : moins d'entités qu'avant, mais rien ne se recharge.
- Une base modifiée n'est pas relue par *Jusqu'au nœud* : *Lancer* (ou *Tout relancer*) la relit.
