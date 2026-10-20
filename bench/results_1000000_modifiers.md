# Benchmark (1 000 000 points, 100 000 parcelles, 10 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| tampon | 99846 | 6.7 | 842 | - |
| clipper | 95909 | 26.6 | 799 | - |
| point_sur_surface | 1000000 | 6.6 | 702 | - |
| relateur | 99846 | 5.4 | 818 | - |
| filtre_spatial | 882812 | 5.9 | 569 | - |
| voisins | 30000 | 10.1 | 764 | - |
| dissolver | 1 | 3.6 | 645 | - |
| dissolver_groupes | 999 | 2.7 | 638 | - |
| superposition | 77990 | 4.1 | 681 | - |
| zonal | 10000 | 14.4 | 632 | - |
