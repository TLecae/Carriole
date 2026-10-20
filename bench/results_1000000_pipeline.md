# Benchmark (1 000 000 points, 100 000 parcelles, 10 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| filtre | 79493 | 1.7 | 404 | 10 / 1500 |
| calcul | 1000000 | 3.2 | 392 | 8 / 1500 |
| reprojection | 1000000 | 5.6 | 608 | 20 / 1500 |
| tri | 1000000 | 5.2 | 611 | - |
| gpkg | 1000000 | 12.8 | 385 | 60 / 1500 |
| csv | 1000000 | 4.1 | 379 | 30 / 1500 |
| chaine | 979987 | 7.7 | 663 | - |
| deux_runs | 1959974 | 11.7 | 1189 | - |
