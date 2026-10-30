# ADR 0045 — Conventions des modificateurs

- **Statut** : accepté
- **Date** : 2026-10-29
- **Complète** : ADR 0041 (modificateurs), 0044 (ports calculés à la demande)

## Contexte
Un tour des 85 modificateurs a relevé des comportements différents pour des situations identiques : message d'un
attribut absent (liste Python `['a']`, texte joint, singulier), port d'erreur (`Rejected`, `Failed`, `NoResult`),
vérifications faites seulement pendant le run, boucles Python par entité, voies SQL absentes sur des opérations
simples. Les comportements suivent ceux des ETL géographiques établis, sous des noms propres à Carriole.

## Décision
1. **Erreurs de configuration** : tout ce qui se vérifie sans données (paramètre vide, attribut absent en amont, SCR
   géographique pour une distance, expression invalide) l'est dans `output_schema` : le run s'arrête avant de lire les
   données. Un attribut absent se signale par `raise missing_attributes(noms)` (`sdk`), « attribut absent : a » ou
   « attributs absents : a, b ».
2. **Erreurs d'une entité** : l'entité part sur `Rejected` avec `_rejection_reason`, jamais une erreur qui arrête le
   run. `Passed` / `Failed` veulent dire « condition vraie / fausse », pas « erreur ». *Appeler une API web* envoie
   donc ses échecs sur `Rejected` (avant : `Failed` avec `_http_error`) ; *Lancer un flux* garde `Succeeded` /
   `Failed` : l'issue du flux lancé, pas une erreur d'entité.
3. **Manque de ressources** (mémoire, disque plein) : erreur du nœud, avec un message qui dit quoi faire
   (`duck.resource_error`), sans rejet d'entités ni repli sur la voie par lots.
4. **Vitesse** : pas de boucle Python sur toutes les lignes ; Arrow, numpy, shapely ou DuckDB. Une boucle ne porte que
   sur ce qui est rare (valeurs illisibles différentes, groupes distincts, géométries invalides). Un modificateur qui
   reçoit toute sa table ne trie pas des lignes larges pour garder leur ordre : il trie les seules clés et recolle par
   position (`StatisticsByGroup`).
5. **Voie SQL** : une opération ligne à ligne exprimable en SQL en a une, avec un test de parité dans
   `test_deferred.py` ; quand les deux voies ne peuvent pas donner exactement le même texte (nombre ou date convertis
   en texte), `sql()` rend `None` pour ces réglages.
6. **Mémoire** : la référence est le budget de *Paramètres → Calcul* ; aucun plafond fixe supplémentaire.
7. **Noms de paramètres** : l'attribut lu s'appelle `attribute` (ou `attributes`), la géométrie retirée
   `remove_geometry`. Un renommage passe par `renames.PARAMS` (ports : `renames.PORTS`) : les flux enregistrés
   s'ouvrent sans retouche et sont réécrits au prochain enregistrement.

## Appliqué le 2026-10-29
- `missing_attributes` dans 22 modificateurs ; `require_attributes` (vérification dans `output_schema`) pour
  Counter, FindDuplicates, OrderBy, PointFromXY, Dissolve, Hull, SetVariables, PivotWider, Buffer ; clés de
  JoinByField des deux côtés ; SCR géographique de Buffer, MeasureArea et MeasureLength signalé avant le run ; le
  schéma déclaré par Counter est celui qu'il écrit (entier).
- `MapValues.source` → `attribute`, `GeometryToAttribute.remove` → `remove_geometry` ; *Appeler une API web* :
  `Failed` → `Rejected`.
- Géométrie absente : « pas de géométrie » pour MeasureArea et MeasureLength (le mauvais type garde son motif) ;
  JoinByNearest sans candidat rejette les entités sans géométrie ; MeasureLength accepte les anneaux (`data.LINES`).
- `on_error` : AddAttributes (reject par défaut, null : attribut vide, voie SQL avec `TRY`) ; ExtractJson
  (null par défaut, comme avant ; reject : JSON invalide sur `Rejected`).
- Déclarés bloquants : Sample, RunFlow, JoinByNearest, Rasterize. Tranches `GEOMETRY_SLICE` : ExtractByLocation,
  JoinByLocation, PointsInPolygons, SampleRasterValues. Simplify ne réencode que les géométries simplifiées.
- Boucles par entité retirées : GeometryFromAttribute, MapValues, FixGeometries, PointFromXY, SplitText, ExtractText,
  ExplodeList, Counter, FindDuplicates, SampleRasterValues, CreateFeatures, colonnes constantes, `reject`.
- Recherche des lignes en erreur d'une expression (`expr.safe_evaluate`) : passage `TRY`, puis examen des seules
  lignes suspectes, table enregistrée une fois.
- Voies SQL, avec test de parité : ReplaceText, MapValues, ExtractJson, Counter (sans groupe), ReplaceNulls
  (attributs texte), FindDuplicates (repérage sur les clés seules, recollage par position), BuildList, ExecuteSql,
  ExplodeList (`UNNEST` de la liste et de ses rangs ; un attribut indice existant est remplacé, plus doublé).
  OrderBy : la voie par lots exécute la requête de la voie SQL, les deux donnent donc le même ordre (texte de
  nombres, espaces, NaN). Le résultat d'ExecuteSql a la même forme par les deux voies (`querier.final_select` :
  géométrie en WKB, entier de 128 bits en DOUBLE comme l'écrit Parquet).
- Transformations pyproj en cache partagé (`data.transformer`, `data.to_crs`).

## Reste à faire
- Voies SQL : ConvertDate (formats de date), ExtractText (positions négatives, regex sans correspondance : vide
  pour Arrow, texte vide pour DuckDB), SplitText (pas de nombre maximal de coupures dans `string_split`),
  PivotLonger, Aggregate (fusion de géométries), PivotWider (deux passages sans colonnes listées).
  ComponentOutput reste sans voie SQL : un port de composant publié doit avoir ses fichiers.
- Reproject : l'absence de SCR source reste vérifiée pendant le run (un schéma peut ne pas porter le SCR d'un
  format dont il est connu à la lecture).
- JoinByField : la sortie jointe est triée avec toutes ses colonnes (plusieurs lignes par entité : le recollage par
  position de StatisticsByGroup ne s'applique pas tel quel).
