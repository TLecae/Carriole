# ADR 0029 — Noyau spatial : préparer la grande géométrie, ne découper que les bords

- **Statut** : accepté
- **Date** : 2026-10-12
- **Complète** : [ADR 0028](0028-performance-memoire-et-runs-paralleles.md)

## Contexte
Essai réel : 1,6 M de parcelles du RPG découpées par la Haute-Garonne (586 communes fusionnées, un polygone de
41 580 sommets). Le Clip s'est arrêté après 157 s sur « GEOS bad allocation », 5,7 Go utilisés. Deux défauts :
- le module spatial commun testait chaque parcelle (préparée) contre le polygone entier, non préparé : O(n) par
  parcelle, 21 s pour 100 000 parcelles. ExtractByLocation, JoinByLocation, PointsInPolygons, CreateGrid et
  OverlayPolygons avaient le même défaut ;
- le Clip fabriquait, pour chaque parcelle touchée, une copie du département (`union_all` d'un seul polygone),
  puis la découpait contre le département entier : ~8 000 copies par lot.

## Décision
1. **La grande géométrie est préparée, pas la petite.** `SpatialIndex.pairs` cherche les paires par emprise
   (arbre), puis fait le test exact en préparant le plus gros des deux côtés : 0,1 s au lieu de 21 s par lot.
2. **Une géométrie préparée n'est jamais testée par deux threads à la fois.** GEOS y garde un état qui change à
   chaque test (index construits au premier usage, détecteur d'intersections) : le partager plante le processus
   (constaté). Chaque thread prépare sa propre copie quand le côté référence est petit (≤ 1 M de sommets préparés,
   le cas courant) ; sinon les géométries sont partagées et testées sous verrou.
3. **L'intersection n'est calculée que là où elle change quelque chose.** Le Clip produit bien
   « clippee ∩ clipper » (Inside) et « clippee − clipper » (Outside). Quand le résultat est connu d'avance, GEOS
   n'est pas appelé : une entité entièrement dans le clipper a pour intersection elle-même (sortie telle quelle,
   octet pour octet), une entité qui ne le touche pas (ou seulement par un bord) a une intersection vide. Seules les
   entités à cheval sont réellement découpées : 148 parcelles sur 100 000 dans le cas réel. Les tests comparent la
   surface de « Inside » à celle de `intersection(clippee, clipper)`.
4. **Découpe locale.** Une géométrie de plus de 1 024 sommets est découpée en carreaux emboîtés (arbre quaternaire,
   ~256 sommets par carreau) ; une entité est découpée contre le plus petit carreau qui la contient
   (« géométrie ∩ carreau » : même résultat, quelques centaines de sommets au lieu de dizaines de milliers).
   OverlayPolygons coupe de même les contours des grands polygones à l'emprise de chaque paquet.
5. **Erreurs isolées.** Si GEOS refuse un lot, chaque paire est refaite seule, puis après réparation ; celles qui
   échouent encore partent sur « Rejected » (le Clip a désormais ce port). Un « bad allocation » de GEOS donne le
   message de mémoire insuffisante.
6. Les traitements géométriques travaillent par tranches de 20 000 lignes (`sdk.slices`) : mémoire bornée par tâche.

## Résultat (cas réel, 1 601 817 parcelles × Haute-Garonne)
Avant : arrêt après 157 s, 5,7 Go. Après : 7 à 8 s pour le Clip seul (8 threads), 1,5 Go ;
131 720 parties dedans, 1 472 471 dehors ; surfaces conservées (3 454,8 + 33 200,0 = 36 654,8 km²).

## Limites
- Le décodage WKB de Shapely (`from_wkb`) garde le verrou global de Python : ~60 % du temps du Clip, d'où un
  gain plafonné vers ×2 en parallèle. Piste : un Python sans GIL (3.13t / 3.14t), si toutes les dépendances
  le permettent.
- Une entité à cheval sur une coupe de l'arbre reçoit le carreau parent (sur la coupe centrale : la géométrie
  entière) : exact, juste plus lent, et rare.
- Les autres modificateurs n'ont pas été repris un par un (demande de l'utilisateur) : ceux qui passent par
  `SpatialIndex` profitent du point 1.
