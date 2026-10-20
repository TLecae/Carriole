# ADR 0037 — Inspecteur : toutes les entités, carte préparée en arrière-plan, les plus grandes de la vue

- **Statut** : accepté ; point 3 remplacé par l'[ADR 0038](0038-carte-fluide-et-run-jusqu-au-noeud.md) (choix par métatuile)
- **Date** : 2026-10-13
- **Remplace** : ADR 0030 point 1 (les N premières entités, champ « Chargées »)
- **Complète** : ADR 0034 (carte par morceaux)

## Contexte
Retour d'usage sur 1,6 million de parcelles (RPG) :
- avec « Chargées : 200 000 », la carte montrait les 200 000 premières entités du fichier, éparpillées sur toute
  la région, puis un échantillon au hasard de celles-ci : ni ce qui est à l'écran, ni ce qui compte ;
- à petite échelle, les petits polygones devenaient des cellules d'un pixel fusionnées par rangées : des **bandes
  horizontales**, illisibles ;
- le nombre d'entités était écrit quatre fois (compte, « Chargées », « premières sur », message de la carte) ;
- une carte impossible à préparer (mémoire) s'affichait « système de coordonnées inconnu » ;
- la table ne permettait ni d'élargir une colonne ni de copier une valeur ou un nom de colonne.

## Décision
1. **Plus de N.** La table travaille sur tout le port ; ses lignes arrivent par pages au fil du défilement (déjà
   le cas) ; filtres, tri, statistiques, export : tout le port. Un seul compte dans la barre (« 11 / 20 entités »).
2. **Carte préparée en arrière-plan**, une fois par port (`<port>.view.parquet`), par un pool de deux tâches dans
   l'API : `info` répond tout de suite avec `map_status` (`ready`, `building`, `failed`, `none`) ; l'interface
   affiche « Préparation de la carte… » à sa place et redemande l'état toutes les 1,5 s. Échec par manque de
   mémoire : `map_error = "memory"`, message clair (la table reste consultable). Tuiles et comptage avant que la
   carte soit prête : 409. Mesure : 34 s pour 1,6 million de parcelles (comme la vue des N premières, sans N).
3. **Les plus grandes entités de la vue.** `in-view` compte les entités de l'écran (filtres compris) par classe de
   taille (`floor(4 · log2(esize))`, `esize` : plus grand côté de l'emprise en EPSG:3857). Au-delà de 50 000, la
   carte dessine les plus grandes classes qui tiennent dans la limite (`min_size`, dans l'URL des tuiles) : les
   mêmes dans toutes les tuiles et à tous les zooms, et le message « Trop d'entités pour la carte : les N plus
   grandes de la vue… ». Les points n'ont pas de taille : s'il faut en retirer, un échantillon stable (`every`).
   Mesure : 1,8 s pour compter 1,6 million d'entités, tuile de zoom 7 limitée : 0,3 s.
4. **Généralisation sans bandes** (vue version 3) : morceaux d'au moins un pixel simplifiés au demi-pixel du zoom
   suivant (au lieu d'un pixel) ; plus petits, **un point par entité et par cellule** (un pixel du zoom suivant),
   sans géométrie : la cellule est dans `bbox`. La tuile n'en garde qu'un par pixel (regroupement SQL) et le dessine
   en carré d'un pixel. Chaque ligne porte `esize`. La sélection d'une entité énorme regroupe ses cellules
   (rangées, puis union de couverture).
5. **Table** : colonnes élargies à la souris (double-clic : ajustées au contenu) ; menu contextuel *Copier la
   valeur*, *Copier la ligne*, *Copier le nom de la colonne* ; `Ctrl+C` sur la cellule choisie (valeur brute : un
   nombre sans séparateur). Dans la table, `Ctrl+C` ne copie plus les nœuds du flux.

## Conséquences
- Vue de 1,6 million de parcelles : 920 Mo (760 Mo avant), effacée avec le cache du run.
- Les vues des N premières d'avant (`view-<N>`, `view-all`) sont effacées à la première ouverture.
- À petite échelle sur une région entière, la carte montre les ~45 000 plus grandes parcelles (plus de 860 m) :
  une image juste de ce qui est dessiné, et l'invitation à zoomer.
