# Benchmark (1 000 000 points, 100 000 parcelles, 10 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites (lues pour « lecture_ ») | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| filtre | 79493 | 0.9 | 236 | 10 / 1500 |
| calcul | 1000000 | 2.4 | 388 | 8 / 1500 |
| tri | 1000000 | 3.3 | 375 | - |
| statistiques | 1000000 | 3.7 | 500 | - |
| chaine | 979987 | 6.9 | 487 | - |
| large_filtre | 143015 | 10.9 | 1265 | - |
