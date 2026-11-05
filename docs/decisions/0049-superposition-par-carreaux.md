# ADR 0049 — Superposition de polygones par carreaux

- **Statut** : accepté
- **Date** : 2026-11-05
- **Remplace** : le découpage par paquets de polygones de `OverlayPolygons`

## Contexte
`OverlayPolygons` calculait l'arrangement par paquets de 5 000 polygones voisins. Chaque paquet gardait les morceaux
dont le plus petit polygone recouvrant était à lui, et lisait tous les polygones qui touchaient les emprises de ses
membres. Deux défauts :

- sous 5 000 polygones, un seul paquet, donc un seul cœur ;
- un polygone à très grande emprise (un réseau d'un seul tenant) faisait lire à son paquet presque tous les polygones
  du jeu. Mesure sur une occupation du sol de 177 000 polygones (6,25 millions de sommets, dont un réseau d'un million) :
  184 s, dont 170 s pour ce seul paquet, sur un cœur.

## Décision
1. **Carreaux selon la densité des sommets.** Un arbre quaternaire coupe l'emprise tant qu'un carreau contient trop de
   sommets de contours, comptés sur une grille de 1 024 × 1 024 cases. Le nombre visé par carreau dépend :
   - du nombre de cœurs : au moins quatre carreaux par worker ;
   - de la part du budget du run (*Paramètres → Calcul*) : chaque carreau en cours, et chaque résultat en attente,
     y tient.
2. **Chaque carreau calcule ses faces** à partir des contours coupés à ses bords et de son propre contour, en
   parallèle. Une face qui ne touche pas le bord du carreau sort telle quelle.
3. **Recollage des faces à cheval.** Les morceaux qui touchent un bord sont réunis à la fin, par groupes de mêmes
   polygones recouvrants. Deux faces voisines d'un arrangement n'en ont jamais les mêmes : seule une limite de carreau
   peut les séparer.
4. **Recollage exact.**
   - Le point où un contour traverse une ligne de la grille est ajouté au contour une seule fois, avant la découpe.
   - Le contour de chaque carreau porte les coins de ses voisins.
   - Les morceaux ont donc les mêmes sommets de part et d'autre d'une limite : la réunion de couverture de GEOS,
     linéaire, suffit (3 s au lieu de 16 pour le réseau d'un million de sommets).
   - GEOS ne vérifie pas cette couverture. Un contrôle le fait : chaque sommet posé sur le bord du carreau de son
     morceau doit appartenir à deux morceaux. Sinon, réunion complète.
5. **Contours en tronçons** d'au plus 4 096 sommets, indexés : un carreau ne coupe que les tronçons qui le traversent.
6. **Contrôle de validité, réparation et contours** sur tous les cœurs, par tranches.

## Conséquences
- Le jeu mesuré passe de 184 s à 32 s, avec les mêmes morceaux. Le polygone géant est réparti entre tous les carreaux
  au lieu d'occuper un cœur.
- Il reste une partie série : la validation d'un polygone d'un million de sommets (5 s), la préparation des contours
  et des carreaux (4 s), l'écriture des résultats.
- L'ordre des morceaux en sortie change (carreau par carreau, puis les faces recollées) ; les morceaux, leurs origines
  et leurs attributs sont les mêmes.
