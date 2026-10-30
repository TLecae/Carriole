# ADR 0046 — Carte : polygones fusionnés simplifiés sans validation et découpés

- **Statut** : accepté
- **Date** : 2026-10-30
- **Complète** : ADR 0034 (carte par morceaux), 0037 (carte préparée en arrière-plan), 0038 (métatuiles)

## Contexte
Avec la sortie d'un *Dissolve* ou d'un *Buffer* fusionné, la carte de l'inspecteur ne s'affichait pas : la préparation
ne finissait pas, ou finissait en « géométries trop volumineuses ». L'ADR 0034 avait réglé le cas d'un multipolygone
fait de milliers de morceaux, en l'éclatant. Il restait le cas d'un polygone **d'un seul tenant**, avec un long contour
et des milliers de trous, que l'éclatement ne divise pas.

Mesures sur un polygone fusionné de 1,57 million de sommets et 21 000 trous (union de 300 000 disques de 160 m) :

- `ST_Simplify` de DuckDB, comme `shapely.simplify`, passe par le Douglas-Peucker de GEOS, qui **valide puis répare**
  chaque polygone simplifié. Le coût croît bien plus vite que le nombre de sommets : 332 s pour le niveau 10, 74 s
  pour le niveau 8. Or la vue simplifie chaque morceau à 6 niveaux.
- Les tuiles de zoom 12 et plus relisaient le polygone entier dans chaque tuile, puis le re-simplifiaient avec la
  même validation.
- Le même polygone, simplifié anneau par anneau comme des lignes (pas de validation pour une ligne), prend 0,1 à
  0,4 s.
- `ST_Subdivide` de DuckDB prend 13 s pour 750 000 sommets. Sur un polygone invalide, il lève une erreur interne qui
  rend la connexion inutilisable.
- `clip_by_rect` de GEOS coupe le contour seul en 0,04 s, mais le polygone avec ses trous en 7,7 s : GEOS cherche
  sans index la pièce de chaque trou.

## Décision
1. **Morceaux énormes à part.** Un morceau de plus de `PIECE_VERTICES` (4 096) sommets quitte la voie SQL de la vue
   (`etl_engine.view`, version 5). Il est traité en Python (shapely, GIL libéré, tous les cœurs), par lots que la
   mémoire de la connexion contient. Les cellules d'un pixel restent calculées en SQL pour tous les morceaux.
2. **Simplification anneau par anneau** (`simplify_polygons`) : Douglas-Peucker sur les anneaux traités comme des
   lignes, puis polygones reconstruits. On retire les trous effondrés ou plus petits qu'un pixel, et les polygones dont
   le contour s'effondre. Rien n'est réparé : un polygone invalide suffit pour dessiner. Les tuiles s'en servent aussi
   pour leurs polygones de plus de 256 sommets. Les petits polygones restent sur `shapely.simplify`, un peu plus
   rapide pour eux.
3. **Découpe** (`subdivide`) : coupes en deux successives sur la grille des tuiles, par `clip_by_rect` (coupe exacte,
   sans jour ni chevauchement). La découpe s'arrête à 4 096 sommets par pièce, ou à une pièce de la taille d'une tuile
   du zoom le plus fin servi par le niveau : zoom 15 pour le niveau complet, L + 1 pour le niveau L.
   - Les petits trous (au plus 256 sommets) sont retirés avant la découpe, puis remis dans leur pièce. Un trou entier
     y est placé par un index spatial de ses points, sur les pièces préparées. Un trou coupé par la case est retiré de
     la pièce par une différence locale.
   - Les grands trous, peu nombreux, sont coupés avec le contour.
4. **Contour à part.** Les pièces d'un polygone découpé sont des surfaces sans bord (`role` 1). Le contour est formé
   des anneaux, traités comme des lignes et découpés eux aussi (`role` 2).
   - Les tuiles portent la propriété `r`. `ST_AsMVT` omet une propriété nulle, si bien que les tuiles des données
     ordinaires ne changent pas.
   - La carte trace le contour des polygones sans `r = 1`, ainsi que les lignes `r = 2`. Les coupes ne se voient pas.
   - La sélection d'une entité énorme renvoie ses pièces et son contour, avec `outline: "edges"`.
5. **Row groups d'au plus 8 Mo** dans la vue (`ROW_GROUP_SIZE_BYTES`). Les 50 000 lignes d'un row group de pièces
   énormes pesaient 97 Mo, décompressés trois fois par tuile, même vide : 0,65 s par tuile.
6. **Préparation sur tous les cœurs.** La connexion `view` garde la mémoire de l'inspecteur, avec tous les threads du
   poste au lieu de la moitié. Chaque préparation note ses étapes et leurs durées dans le journal du serveur.
7. **Délai partout dans une tuile** : toutes ses requêtes passent par `Duck.guarded`, qui renvoie 504 au-delà de
   `QUERY_TIMEOUT`. La carte signale une tuile en échec au lieu de laisser une zone vide sans rien dire.
8. **Affichage** :
   - les pièces découpées sont dessinées sans anticrénelage (`fill-antialias`), qui traçait le bord de chaque pièce
     dans la couleur de la surface, donc les coupes ; la surface de la sélection aussi (son bord est sa ligne) ;
   - « Chargement de la carte… » ne suit plus que les sources de données : avec un fond de plan injoignable (hors
     ligne, proxy), il ne disparaissait jamais.

## Mesures (`bench/map.py`, poste de développement, 12 cœurs)

| Jeu | Préparation avant | Préparation après | Tuiles après (médiane, z5 à z14) |
| --- | ---: | ---: | ---: |
| Polygone fusionné (1,57 M de sommets, 21 000 trous) | 864 s | 12 s | 0,08 à 0,21 s |
| 1 M de parcelles | 11,7 s | 11 s | 0,08 à 0,47 s |
| 1 M de points | 1,5 s | 1,2 s | 0,04 à 0,12 s |

## Conséquences
- Une vue de version 4 est reconstruite à la première ouverture du port.
- Données ordinaires : préparation inchangée (le temps va au tri final des lignes avec leur géométrie, puis à
  l'écriture) ; tous les cœurs n'y changent rien de mesurable sur ce poste.
- La sélection d'une entité énorme reste généralisée à environ 1 000 pixels sur son emprise (ADR 0034) : en zoomant
  fort, ses contours sont grossiers.
- Un polygone énorme simplifié peut être invalide (trous qui touchent le contour, anneaux qui se croisent) : seul le
  dessin s'en sert. Dans une pièce invalide, un petit trou coupé par la case peut rester plein.
