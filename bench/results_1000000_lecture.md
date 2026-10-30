# Benchmark (1 000 000 points, 100 000 parcelles, 10 000 zones)

Machine : Intel64 Family 6 Model 85 Stepping 4, GenuineIntel, 12 cœurs logiques, 16 Go, Python 3.12.11, Windows.

| Cas | Entités écrites (lues pour « lecture_ ») | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |
| --- | ---: | ---: | ---: | --- |
| lecture_parquet | 1000000 | 0.5 | 321 | - |
| lecture_csv | 1000000 | 1.3 | 443 | - |
| lecture_gpkg | 1000000 | 1.0 | 240 | - |
| lecture_shp | 1000000 | 12.7 | 229 | - |
| lecture_geojson | 1000000 | 24.8 | 265 | - |
| lecture_fgb | 1000000 | 7.8 | 206 | - |
