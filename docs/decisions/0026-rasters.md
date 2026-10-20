# ADR 0026 — Rasters : une table de rasters, des pixels hors d'Arrow

- **Statut** : accepté
- **Date** : 2026-10-12

## Contexte
Il faut lire, calculer et écrire des rasters (MNT en dalles, scènes Sentinel-2 d'un catalogue STAC) dans le même
graphe que les entités, sans refaire le moteur ni charger des images entières en mémoire.

## Décisions
- **Un port raster est une table Arrow dont chaque ligne est un raster.** Colonnes : `raster` (struct : `uri`, `driver`,
  `width`, `height`, `count`, `dtype`, `nodata`, `transform`, `crs`, `band_names`), `geometry` (emprise, dans le SCR du
  raster) et les attributs (date, couverture nuageuse, nom du fichier…). Aucun nouveau type de port : `ExtractByExpression`, `OrderBy`,
  les jointures et l'inspecteur fonctionnent tels quels sur les propriétés d'un raster. Un modificateur raster refuse une
  entrée sans colonne `raster` (message d'analyse).
- **Les pixels ne passent jamais par Arrow.** Une ligne désigne un fichier : source référencée sur place, ou fichier
  produit par le nœud dans `ctx.work_dir` (dossier `rasters/` du nœud, dans le cache du run), en GeoTIFF par tuiles
  compressé. Les sélections de bandes et les piles sont des **VRT écrits à la main** (pas de copie des pixels).
- **Traitement par fenêtres** (`raster.core.map_windows`) : blocs de 512 px alignés sur la grille, répartis sur des fils
  (rasterio relâche le GIL), mémoire bornée à *blocs × fils*. Mesure : NDVI sur 6000 × 6000 × 2 bandes en 4,4 s pour
  +108 Mo de pic (test `slow`, plafond 700 Mo).
- **Expressions de bandes avec `numexpr`** (multifil, sans `eval` Python) ; les noms de bandes servent de variables.
  Seules les fonctions de numexpr sont disponibles.
- **STAC avec `pystac-client`** : chaque scène devient une ligne, les assets choisis deviennent les bandes d'un VRT
  distant (`/vsicurl/`, lecture par plages, rien n'est téléchargé en entier). Signature Planetary Computer en option.
  Réglages GDAL HTTP centralisés (`gdal_env`) : proxy du poste, multirange, cache.
- **Alignement** : un calcul à plusieurs rasters prend la grille du premier ; les autres sont déformés à la volée
  (VRT). `ResampleRaster` rend l'alignement explicite quand on veut le contrôler.
- **Inspecteur** : table des rasters, emprises sur la carte, **tuiles PNG calculées à la demande**
  (`/tiles/raster/{run}/{nœud}/{port}/{ligne}/{z}/{x}/{y}.png`, déformation en Web Mercator par VRT, étirement par
  percentiles 2-98 d'une lecture réduite mise en cache, palette ou composition RVB), et **valeur de chaque bande au
  pixel cliqué** (`/api/data/…/raster/{ligne}/pixel`). Encodeur PNG écrit à la main, pas de dépendance de plus. La CSP
  n'a pas changé (même origine).
- **Dépendances ajoutées** : `rasterio` (sa propre GDAL), `numexpr`, `pystac-client`, `contourpy`, `httpx`. `rio-tiler`
  prévu au plan n'est pas retenu : l'encodeur PNG et la déformation fournis par rasterio suffisent.
- **Le catalogue passe à 85 modificateurs** (47 + 21 + 17), non 80 : les 17 rasters sont ceux du plan, les 21 du J13 aussi.

## Conséquences
- Un fichier raster supprimé ou déplacé après le run rend la ligne illisible dans l'inspecteur (message clair) ; le cache
  du run garde les fichiers produits par les nœuds.
- Les noms de bandes ne sont connus qu'à l'exécution : le schéma de sortie de `ZonalStatistics` est inconnu à l'analyse
  quand « Bandes » est vide.
- Les rasters tournés (matrice de transformation avec cisaillement) sont refusés par les opérations qui supposent une
  grille alignée sur les axes.
- Les accès réseau (STAC, COG distants) passent par le proxy du poste ; hors ligne, seuls les rasters locaux fonctionnent.
