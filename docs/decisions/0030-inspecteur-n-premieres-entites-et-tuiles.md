# ADR 0030 — Inspecteur : les N premières entités, tableau et carte en tuiles vectorielles

- **Statut** : accepté ; point 1 (N premières entités) remplacé par l'[ADR 0037](0037-inspecteur-toutes-les-entites.md)
- **Date** : 2026-10-12
- **Remplace** : le point 7 de l'[ADR 0028](0028-performance-memoire-et-runs-paralleles.md) (carte en GeoJSON) ;
  **reprend** la vue et les tuiles de l'[ADR 0010](0010-cache-geoparquet-et-tuiles.md), construites à la demande

## Contexte
Au J17, la carte recevait les N premières entités en GeoJSON (2 000 par défaut, 50 000 au plus). Trop peu pour un
port de plusieurs centaines de milliers d'entités. L'utilisateur demande le retour des tuiles vectorielles, avec un
nombre d'entités réglable à la main, qui commence bien plus haut, et qui vaut aussi pour le tableau.

## Décision
1. **N, commun au tableau et à la carte.** L'inspecteur travaille sur les N premières entités du port (ordre du
   port) : pages, filtres, tri, statistiques, export et tuiles. Champ « Chargées » de la barre de l'inspecteur,
   100 000 par défaut, retenu d'une session à l'autre ; vide ou « Tout charger » : toutes. Chaque appel de données
   et chaque URL de tuile porte `n` (0 : toutes).
2. **Vue construite à la demande, jamais pendant le run.** À la première demande d'un port géométrique pour un N
   donné, l'API écrit `<port>.view-<N>.parquet` (`view-all` : toutes) : les N premières entités, reprojetées une
   seule fois en EPSG:3857, avec `bbox` et `fid`, triées Hilbert (sur l'emprise du monde : une seule passe).
   Deux demandes simultanées n'en construisent qu'une ; une seule vue est gardée par port. Mesuré sur le RPG
   (1,6 M de parcelles) : 3 s pour 100 000, 5 s pour 500 000, 16 s pour tout.
3. **Tuiles vectorielles** (`/tiles/…/{z}/{x}/{y}.pbf?q=…&color=…&n=…`), mises en cache sur disque. Les géométries
   de la tuile sont calculées par Shapely, vectorisé : passage en coordonnées de tuile, découpe, simplification à
   deux unités, arrondi, sans réparation ; une entité plus petite qu'un pixel devient un carré d'un pixel (elle reste
   visible). DuckDB n'encode plus que la tuile (`ST_AsMVT`). `ST_AsMVTGeom` réparait chaque polygone replié par
   l'arrondi : ~1 ms par entité à petite échelle (24 s pour une tuile du RPG, 1,3 s maintenant). Au-delà de 20 000
   entités dans une tuile, un échantillon.
4. L'entité sélectionnée est surlignée à part, avec sa géométrie complète (pas celle, simplifiée, de la tuile).
5. Les ports sans géométrie : le tableau suit N aussi (`file_row_number < N`), sans vue. Les rasters lisent le
   port lui-même (ni vue, ni N).

## Conséquences
- Le coût de l'inspecteur reste hors du run, et proportionnel à N.
- Une vue occupe à peu près la taille du port (géométrie reprojetée en plus) : une seule par port.
- `mapbox-vector-tile` revient en dépendance de test (décodage des tuiles dans les tests de l'API).
