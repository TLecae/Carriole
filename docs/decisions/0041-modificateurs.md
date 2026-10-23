# ADR 0041 — « Modificateurs » et noms originaux

- **Statut** : accepté
- **Date** : 2026-10-20
- **Modifie** : ADR 0008 (SDK), et les noms de types de toutes les ADR précédentes

## Contexte
Carriole est publié en open source (ADR 0039). Ses briques s'appelaient « transformers » et leurs noms techniques
(`AttributeCreator`, `Tester`, `Bufferer`…, ports `requestor` / `supplier`, `clippee` / `clipper`) reprenaient ceux
d'un logiciel commercial. L'auteur veut un vocabulaire propre au projet.

## Décision
1. **Le concept s'appelle « modifier »** dans le code et en anglais, **« modificateur »** en français : classe
   `etl_engine.sdk.Modifier`, paquet `etl_engine.modifiers`, commande `etl modifiers`, API `/api/modifiers`,
   point d'entrée de plugin `etl.modifiers`, assistant (`write_modifier`, `describe_modifier`).
2. **Noms techniques originaux**, d'après le vocabulaire courant des outils SIG (algorithmes de traitement de QGIS
   notamment) : `AddAttributes`, `ExtractByExpression`, `Buffer`, `Clip`, `Dissolve`, `JoinByLocation`,
   `JoinByField`, `LookupJoin`, `FixGeometries`, `CountPointsInPolygon`, `ExecuteSql`… Gardés tels quels : les
   noms génériques (`Counter`, `RasterCalculator`, `ZonalStatistics`, `STACSearch`) et les lecteurs / écrivains de
   formats (`CsvReader`, `PostgresWriter`…).
3. **Ports renommés** quand ils reprenaient ces noms : `Clip` (`input`, `overlay`), `ClipRasterByMask` (`mask`),
   `LookupJoin` (`input`, `lookup` ; `Joined`, `Unjoined`, `UnusedLookup` ; paramètres `input_keys`,
   `lookup_keys`), sorties `output` pour `Aggregate`, `Dissolve`, `OverlayPolygons`.
4. **Migration à la lecture** (`etl_engine/renames.py`) : un flux, un composant publié ou un flux en base enregistré
   avant s'ouvre et s'exécute sans retouche ; il est réécrit avec les nouveaux noms au prochain enregistrement. Les
   identifiants de nœuds (`Tester_1`…) ne changent pas.
5. **Compatibilité des plugins** : `Transformer` (SDK), `run_transformer` (tests), le point d'entrée
   `etl.transformers` et la section `transformers` des catalogues de traduction restent acceptés.

## Conséquences
- Les flux écrits avant le renommage restent lisibles ; ceux écrits après ne s'ouvrent plus dans une version
  antérieure.
