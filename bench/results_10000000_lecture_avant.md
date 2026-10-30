# Benchmark (10 000 000 points, 1 000 000 parcelles, 100 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites (lues pour « lecture_ ») | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| lecture_parquet | 10000000 | 11.6 | 573 | - |
| lecture_csv | 10000000 | 66.8 | 976 | - |
