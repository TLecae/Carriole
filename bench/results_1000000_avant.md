# Benchmark (1 000 000 points, 100 000 parcelles, 10 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| filtre | 79493 | 6.4 | 426 | 10 / 1500 |
| calcul | 1000000 | 7.3 | 472 | 8 / 1500 |
| reprojection | 1000000 | 7.6 | 695 | 20 / 1500 |
| tri | 1000000 | 5.3 | 714 | - |
| statistiques | 1000000 | 5.5 | 842 | - |
| gpkg | 1000000 | 16.5 | 531 | 60 / 1500 |
| csv | 1000000 | 5.9 | 512 | 30 / 1500 |
| chaine | 979987 | 16.3 | 832 | - |
| tampon | 99846 | 8.5 | 995 | - |
| clipper | 95909 | 34.2 | 725 | - |
| point_sur_surface | 1000000 | 13.0 | 995 | - |
| relateur | 99846 | 5.1 | 820 | - |
| filtre_spatial | 882812 | 10.6 | 729 | - |
| voisins | 30000 | 15.4 | 734 | - |
| dissolver | 1 | 12.5 | 568 | - |
| dissolver_groupes | 999 | 2.7 | 520 | - |
| superposition | 77990 | 5.5 | 706 | - |
| zonal | 10000 | 38.5 | 562 | - |
| deux_runs | 1959974 | 23.4 | 1306 | - |
