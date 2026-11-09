# ADR 0050 — Budget de cœurs

- **Statut** : accepté
- **Date** : 2026-11-08
- **Complète** : ADR 0028 (performance, mémoire et runs parallèles), 0045 (conventions des modificateurs)

## Contexte
La mémoire du moteur se règle dans *Paramètres → Calcul* ; les cœurs non. Chaque partie du moteur choisissait les
siens : tous les cœurs du poste pour DuckDB et GDAL, la moitié (8 au plus) pour les lots d'un nœud, 8 nœuds au plus,
`os.cpu_count()` pour l'inspecteur. Sur un gros poste, une partie des cœurs restait inutilisée ; sur un petit, un run
prenait tout le processeur et l'interface ne répondait plus.

## Décision
1. Un réglage *Cœurs du moteur* (`cpu_budget`, variable `ETL_CPU_BUDGET`), à côté de la mémoire. Vide : 60 % des
   cœurs logiques du poste (`duck.CPU_SHARE`), arrondis, au moins un ; jamais plus que le poste.
2. `duck.machine_threads()` rend ce budget ; tout calcul parallèle en découle, sans autre plafond fixe :
   - part de chaque run : budget ÷ runs en cours (worker, lancements par lots) ;
   - dans un run : threads de DuckDB, nœuds exécutés en même temps, threads d'Arrow (`pa.set_cpu_count`), de GDAL
     (`GDAL_NUM_THREADS`, compression des GeoTIFF) et fenêtres raster ;
   - lots ou paquets d'un nœud (`ctx.workers`) : les cœurs du run ÷ les nœuds lourds qui tournent ensemble (deux au
     plus) ; un nœud lourd seul les a tous ;
   - inspecteur et carte : la moitié du budget par requête, le budget pour préparer une carte.
3. Le panneau *Calcul* montre le calcul : cœurs du poste, valeur par défaut, part d'un run selon *Runs en parallèle*,
   partage entre nœuds lourds, et un conseil (garder 2 ou 3 cœurs libres pour travailler à côté).

## Conséquences
- Un nœud lourd seul dans son run (cas courant : une superposition, une fusion) utilise tous les cœurs du run, plus
  seulement la moitié et 8 au plus.
- Les appels réseau gardent leur propre concurrence (*Appeler une API web*, STAC, lecture Parquet par https) : elle
  dépend du serveur, pas du processeur.
