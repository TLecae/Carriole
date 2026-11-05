# Catalogue des modificateurs

Page générée par `etl modifiers doc` : ne pas la modifier à la main.

| Catégorie | Modificateurs |
| --- | --- |
| Attributs | [Construire une liste](#buildlist), [Convertir le type d'attributs](#castattributes), [Convertir une date](#convertdate), [Créer des attributs](#addattributes), [Découper un texte](#splittext), [Dépivoter (colonnes en lignes)](#pivotlonger), [Extraire du JSON](#extractjson), [Extraire du texte](#extracttext), [Garder des attributs](#retainattributes), [Gérer des attributs](#refactorattributes), [Pivoter (lignes en colonnes)](#pivotwider), [Remplacer du texte](#replacetext), [Renommer des attributs](#renameattributes), [Renommer en masse](#renameattributesbypattern), [Retirer des attributs](#dropattributes), [Table de correspondance](#mapvalues), [Valeurs vides](#replacenulls), [Éclater une liste](#explodelist) |
| Calculs | [Agréger](#aggregate), [Calculer des statistiques](#statisticsbygroup), [Code Python](#pythonscript), [Compteur](#counter), [Requête SQL sur les entrées](#executesql), [Trier](#orderby) |
| Composants | [Entrée du composant](#componentinput), [Sortie du composant](#componentoutput) |
| Entrées / sorties | [Appeler une API web](#httprequest), [Créer des entités](#createfeatures), [SQL (créateur)](#databasequery) |
| Filtres | [Filtre par type de géométrie](#splitbygeometrytype), [Filtre spatial](#extractbylocation), [Filtre à plusieurs sorties](#routebyexpression), [Filtrer les doublons](#findduplicates), [Filtrer par expression](#extractbyexpression), [Échantillonner](#sample) |
| Géométrie | [Attribut vers géométrie](#geometryfromattribute), [Carroyage](#creategrid), [Créer un point / un sommet](#pointfromxy), [Découper](#clip), [Enveloppe des géométries](#hull), [Extraire des coordonnées](#extractcoordinates), [Fusionner (dissoudre)](#dissolve), [Géométrie vers attribut](#geometrytoattribute), [Longueur de lignes](#measurelength), [Remplacer par un point central](#centroids), [Reprojeter](#reproject), [Réparer les géométries](#fixgeometries), [Simplifier les géométries](#simplify), [Superposition de polygones](#overlaypolygons), [Surface et périmètre](#measurearea), [Valider les géométries](#checkvalidity), [Zone tampon](#buffer), [Éclater les géométries multiples](#multiparttosingleparts) |
| Jointures | [Jointure attributaire](#joinbyfield), [Plus proches voisines](#joinbynearest), [Point dans polygone](#pointsinpolygons), [Relateur spatial](#joinbylocation), [SQL (par entité)](#databasequeryperfeature) |
| Lecture | [Lecture CSV](#csvreader), [Lecture Excel](#excelreader), [Lecture FlatGeobuf](#flatgeobufreader), [Lecture GeoJSON](#geojsonreader), [Lecture GeoPackage](#geopackagereader), [Lecture (Geo)Parquet](#geoparquetreader), [Lecture MapInfo](#mapinforeader), [Lecture Oracle](#oraclereader), [Lecture PostgreSQL / PostGIS](#postgresreader), [Lecture SQLite / SpatiaLite](#spatialitereader), [Lecture Shapefile](#shapefilereader) |
| Lots et variables | [Lancer un flux](#runflow), [Lire les fichiers listés](#readlistedfiles), [Lister des fichiers](#listfiles), [Poser des variables](#setvariables), [Écrire un fichier par groupe](#splittofiles) |
| Rasters | [Analyse de terrain (pente, exposition, ombrage)](#terrainanalysis), [Calculatrice de rasters](#rastercalculator), [Courbes de niveau](#contour), [Découpe de rasters](#cliprasterbymask), [Empiler des bandes](#stackbands), [Entités vers raster](#rasterize), [Lecture de rasters](#rasterreader), [Mosaïque et composite de rasters](#mergerasters), [Raster vers polygones](#polygonize), [Recherche STAC](#stacsearch), [Reclasser un raster](#reclassifyraster), [Reprojection de rasters](#warpraster), [Rééchantillonner / aligner](#resampleraster), [Statistiques de rasters](#rasterstatistics), [Statistiques zonales](#zonalstatistics), [Valeur du raster sous des points](#samplerastervalues), [Écriture de rasters](#rasterwriter) |
| Écriture | [Écriture CSV](#csvwriter), [Écriture Excel](#excelwriter), [Écriture FlatGeobuf](#flatgeobufwriter), [Écriture GeoJSON](#geojsonwriter), [Écriture GeoPackage](#geopackagewriter), [Écriture (Geo)Parquet](#geoparquetwriter), [Écriture MapInfo](#mapinfowriter), [Écriture Oracle](#oraclewriter), [Écriture PostgreSQL / PostGIS](#postgreswriter), [Écriture SQLite / SpatiaLite](#spatialitewriter), [Écriture Shapefile](#shapefilewriter) |

## Attributs

<a id="buildlist"></a>

### Construire une liste — `BuildList`

Regroupe les valeurs d'un attribut dans une liste : une entité par groupe, avec la liste des valeurs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont regroupées par « Regrouper par » (vide = un seul groupe) ; la liste réunit les valeurs de « Attribut des valeurs », dans l'ordre d'arrivée (ou triées, ou sans doublon). Les valeurs vides sont ignorées. Géométrie : « none » (aucune) ou « first » (celle de la première entité du groupe). La liste est un attribut de type liste : « Éclater une liste » fait l'inverse.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="buildlist-group_by"></a>Regrouper par | Vide = un seul groupe pour toutes les entités. |  |
| <a id="buildlist-attribute"></a>Attribut des valeurs | Les valeurs qui vont dans la liste. |  |
| <a id="buildlist-list_name"></a>Attribut liste | Nom de la liste créée (vide = `attribut` + _liste). |  |
| <a id="buildlist-distinct"></a>Sans doublon | Une seule fois chaque valeur. | false |
| <a id="buildlist-sort"></a>Trier | Valeurs triées ; sinon dans l'ordre d'arrivée. | false |
| <a id="buildlist-geometry"></a>Géométrie | none : aucune ; first : celle de la première entité du groupe. | `none` |

<a id="castattributes"></a>

### Convertir le type d'attributs — `CastAttributes`

Change le type de plusieurs attributs à la fois : texte, entier, décimal, booléen, date.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne convertit un attribut en place. Une valeur impossible à convertir (« abc » en entier) envoie l'entité sur « Rejected » avec le motif, sauf si « Valeur non convertible » vaut null : elle devient alors vide. « Virgule décimale » lit les nombres à la française (« 1 234,5 »). Booléens reconnus : true/false, oui/non, 1/0. Une valeur vide reste vide.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="castattributes-conversions"></a>Conversions | Une ligne par attribut converti. |  |
| <a id="castattributes-on_error"></a>Valeur non convertible | reject : l'entité part sur « Rejected » ; null : la valeur devient vide. | `reject` |

<a id="convertdate"></a>

### Convertir une date — `ConvertDate`

Lit une date écrite en texte (ou un horodatage) et la convertit : date, date-heure, texte formaté, secondes Unix.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« Format de lecture » est un format strptime (%d/%m/%Y, %Y%m%d, %Y-%m-%d %H:%M:%S…) ; vide = lecture automatique des formats ISO (2026-10-11, 2026-10-11T08:30:00). Un attribut déjà de type date ou date-heure n'a pas besoin de format. Résultat : date, date-heure, texte (avec « Format d'écriture », ex. %d/%m/%Y) ou epoch (secondes depuis 1970, en UTC). Une valeur illisible envoie l'entité sur « Rejected », ou devient vide si « Valeur illisible » vaut null. Codes : %Y année, %m mois, %d jour, %H heure, %M minute, %S seconde, %B nom du mois, %A nom du jour.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="convertdate-attribute"></a>Attribut | Attribut qui contient la date. |  |
| <a id="convertdate-from_format"></a>Format de lecture | Format strptime du texte, ex. %d/%m/%Y ; vide = ISO automatique. |  |
| <a id="convertdate-to"></a>Résultat | date, datetime, text ou epoch (secondes Unix, UTC). | `date` |
| <a id="convertdate-to_format"></a>Format d'écriture | Pour « text » : format strftime, ex. %d/%m/%Y. | `%Y-%m-%d` |
| <a id="convertdate-destination"></a>Attribut résultat | Vide = remplace l'attribut d'origine. |  |
| <a id="convertdate-on_error"></a>Valeur illisible | reject : l'entité part sur « Rejected » ; null : la valeur devient vide. | `reject` |

<a id="addattributes"></a>

### Créer des attributs — `AddAttributes`

Ajoute ou remplace des attributs calculés par des expressions SQL.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque attribut est calculé par une expression SQL DuckDB, dans l'ordre : une expression peut utiliser les attributs créés plus haut. Exemples : "surface" * 2 ; upper("nom") ; CASE WHEN "pop" > 1000 THEN 'ville' ELSE 'village' END ; ST_Area(geometry). Une entité dont l'expression échoue (ou dont la valeur ne se convertit pas dans le type demandé) part sur « Rejected » avec le motif ; avec « En cas d'échec : null », l'attribut reste vide.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="addattributes-attributes"></a>Attributs à créer | Calculés dans l'ordre : chacun peut utiliser les précédents. |  |
| <a id="addattributes-on_error"></a>En cas d'échec | reject : l'entité part sur « Rejected » avec le motif ; null : l'attribut reste vide. | `reject` |

<a id="splittext"></a>

### Découper un texte — `SplitText`

Découpe un attribut texte selon un séparateur, en plusieurs attributs ou en une liste.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« columns » crée `préfixe_1`, `préfixe_2`… (nombre fixé, pour que le schéma soit connu avant l'exécution) : s'il y a plus de morceaux que de colonnes, le reste est laissé dans la dernière ; s'il y en a moins, les colonnes en trop sont vides. « list » produit une liste (à éclater avec ExplodeList). Un texte vide donne des morceaux vides.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="splittext-attribute"></a>Attribut à découper | Attribut texte à découper. |  |
| <a id="splittext-separator"></a>Séparateur | Texte exact (pas une expression régulière). | `,` |
| <a id="splittext-mode"></a>Résultat | columns : une colonne par morceau (préfixe_1, préfixe_2…) ; list : une seule colonne de type liste, à éclater ensuite avec « Éclater une liste ». | `columns` |
| <a id="splittext-prefix"></a>Préfixe des attributs créés | Vide = nom de l'attribut d'origine. |  |
| <a id="splittext-parts"></a>Nombre de colonnes (mode columns) | Nombre de colonnes créées : le surplus reste dans la dernière, les colonnes en trop sont vides. | `2` |
| <a id="splittext-trim"></a>Retirer les espaces autour de chaque morceau | Retire les espaces au début et à la fin de chaque morceau. | true |

<a id="pivotlonger"></a>

### Dépivoter (colonnes en lignes) — `PivotLonger`

Transforme des colonnes en lignes : une entité par colonne choisie, avec son nom et sa valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque entité reçue donne une entité par attribut de « Attributs à dépivoter » : les autres attributs (et la géométrie) sont recopiés, « Attribut du nom » reçoit le nom de la colonne d'origine et « Attribut de la valeur » sa valeur. Si les colonnes n'ont pas le même type, les valeurs deviennent du texte. Les valeurs vides sont ignorées sauf si « Garder les valeurs vides » est coché. Exemple : (commune, pop_2020, pop_2021) devient (commune, annee, population).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="pivotlonger-attributes"></a>Attributs à dépivoter | Les colonnes qui deviennent des lignes. |  |
| <a id="pivotlonger-name_attribute"></a>Attribut du nom | Reçoit le nom de la colonne d'origine. | `attribute` |
| <a id="pivotlonger-value_attribute"></a>Attribut de la valeur | Reçoit la valeur de la colonne. | `value` |
| <a id="pivotlonger-keep_nulls"></a>Garder les valeurs vides | Décoché : une valeur vide ne donne pas d'entité. | false |

<a id="extractjson"></a>

### Extraire du JSON — `ExtractJson`

Lit un attribut qui contient du JSON : extrait des valeurs dans de nouveaux attributs, ou éclate un tableau.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne de « Valeurs à extraire » lit un chemin (a.b, a[0].b, $.a.b) et le range dans un attribut typé. Avec « Tableau à éclater » (par exemple items ou $.results), le JSON de chaque entité donne **une entité par élément du tableau** : les chemins sont alors lus dans l'élément (chemin vide = l'élément entier, en texte), et les autres attributs sont recopiés. Un chemin absent ou une valeur non convertible donne une valeur vide ; un JSON invalide aussi, ou l'entité part sur « Rejected » si « JSON invalide » vaut reject. Typique après un appel HTTP (HttpRequest).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="extractjson-attribute"></a>Attribut JSON | Attribut texte qui contient le JSON. |  |
| <a id="extractjson-fields"></a>Valeurs à extraire | Un chemin et l'attribut qui le reçoit. |  |
| <a id="extractjson-explode"></a>Tableau à éclater | Chemin d'un tableau : une entité par élément ; vide = une entité par ligne. |  |
| <a id="extractjson-drop_source"></a>Retirer l'attribut JSON | Retire l'attribut d'origine du résultat. | false |
| <a id="extractjson-on_error"></a>JSON invalide | null : les valeurs extraites sont vides ; reject : l'entité part sur « Rejected ». | `null` |

<a id="extracttext"></a>

### Extraire du texte — `ExtractText`

Extrait une partie d'un attribut texte : sous-chaîne, avant/après un séparateur, ou captures d'une expression régulière.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« substring » : à partir de la position « Début » (1 = premier caractère), sur « Longueur » caractères (vide = jusqu'à la fin) ; un début négatif compte depuis la fin. « before » / « after » : ce qui précède / suit la première occurrence du séparateur (vide si absent). « regex » : une expression régulière avec des groupes **nommés** `(?P<code>\d{5})` ; chaque groupe donne un attribut (préfixé par « Préfixe »), vide si le texte ne correspond pas. Syntaxe RE2. Exemple : `(?P<dep>\d{2})(?P<com>\d{3})` sur un code INSEE.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="extracttext-attribute"></a>Attribut | Attribut texte à analyser. |  |
| <a id="extracttext-mode"></a>Extraction | substring, before, after ou regex. | `substring` |
| <a id="extracttext-start"></a>Début (substring) | Position du premier caractère (1 = premier ; négatif depuis la fin). | `1` |
| <a id="extracttext-length"></a>Longueur (substring) | Nombre de caractères ; vide = jusqu'à la fin. |  |
| <a id="extracttext-separator"></a>Séparateur (before / after) | Texte exact (pas une expression régulière). |  |
| <a id="extracttext-pattern"></a>Expression (regex) | Expression régulière RE2 avec des groupes nommés (?P<nom>…). |  |
| <a id="extracttext-destination"></a>Attribut créé | substring / before / after : nom du résultat (vide = `attribut` + _extrait). |  |
| <a id="extracttext-prefix"></a>Préfixe (regex) | Ajouté devant le nom de chaque groupe. |  |

<a id="retainattributes"></a>

### Garder des attributs — `RetainAttributes`

Ne conserve que les attributs choisis, dans l'ordre indiqué.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La géométrie est conservée sauf si on la retire explicitement. Un attribut absent est une erreur.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="retainattributes-attributes"></a>Attributs à garder | Dans cet ordre. |  |
| <a id="retainattributes-keep_geometry"></a>Garder la géométrie | Décocher pour retirer aussi la géométrie. | true |

<a id="refactorattributes"></a>

### Gérer des attributs — `RefactorAttributes`

Crée (expression), renomme ou supprime des attributs, règle après règle.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Les règles s'appliquent dans l'ordre. « set » : calcule l'attribut par une expression SQL (ex. "surface" / 10000) ; « rename » : attribut → nouveau nom ; « remove » : supprime l'attribut. Une entité dont l'expression échoue part sur « Rejected » avec le motif.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="refactorattributes-rules"></a>Règles | Appliquées dans l'ordre. |  |

<a id="pivotwider"></a>

### Pivoter (lignes en colonnes) — `PivotWider`

Transforme les valeurs d'un attribut en colonnes : une ligne par groupe, une colonne par valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont regroupées par « Regrouper par » ; chaque valeur de « Attribut à pivoter » devient une colonne (préfixe + valeur), remplie avec « Attribut des valeurs » combiné par la fonction choisie : first (la première), sum, mean, min, max, count (nombre de lignes) ou concat (valeurs réunies par une virgule). Pour que le schéma soit connu avant l'exécution (formulaires des nœuds suivants), lister les valeurs attendues dans « Colonnes » ; sinon elles sont découvertes dans les données, par ordre alphabétique. La géométrie n'est pas conservée. Exemple : (commune, annee, population) devient (commune, pop_2020, pop_2021, …).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="pivotwider-group_by"></a>Regrouper par | Attributs qui identifient une ligne du résultat. |  |
| <a id="pivotwider-pivot_attribute"></a>Attribut à pivoter | Ses valeurs deviennent les noms des colonnes. |  |
| <a id="pivotwider-value_attribute"></a>Attribut des valeurs | Valeur placée dans chaque colonne (inutile avec count). |  |
| <a id="pivotwider-function"></a>Fonction | Comment combiner plusieurs lignes d'un même groupe et d'une même valeur. | `first` |
| <a id="pivotwider-prefix"></a>Préfixe des colonnes | Ajouté devant chaque valeur pivotée. |  |
| <a id="pivotwider-columns"></a>Colonnes | Valeurs attendues (donnent le schéma avant l'exécution) ; vide = découvertes dans les données. |  |

<a id="replacetext"></a>

### Remplacer du texte — `ReplaceText`

Remplace du texte dans des attributs : texte exact ou expression régulière, en place ou dans un nouvel attribut.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque attribut choisi est converti en texte puis traité. Texte exact : toutes les occurrences de « Chercher » sont remplacées. Expression régulière (syntaxe RE2) : « Remplacer par » peut citer les groupes (\1, \2). « Valeur entière » remplace seulement les valeurs égales à « Chercher » (par exemple « N/A » par une valeur vide). Avec un suffixe, le résultat va dans un nouvel attribut `nom` + suffixe et l'original est conservé. Les valeurs vides restent vides.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="replacetext-attributes"></a>Attributs | Attributs à traiter. |  |
| <a id="replacetext-find"></a>Chercher | Texte exact, ou expression régulière si « Expression régulière » est coché. |  |
| <a id="replacetext-replace"></a>Remplacer par | Vide = supprimer le texte trouvé. |  |
| <a id="replacetext-regex"></a>Expression régulière | « Chercher » est une expression régulière (RE2). | false |
| <a id="replacetext-whole_value"></a>Valeur entière | Remplace seulement les valeurs entières égales à « Chercher ». | false |
| <a id="replacetext-ignore_case"></a>Ignorer la casse | Ne distingue pas majuscules et minuscules. | false |
| <a id="replacetext-suffix"></a>Suffixe du nouvel attribut | Vide = remplace l'attribut ; sinon crée `nom` + suffixe. |  |

<a id="renameattributes"></a>

### Renommer des attributs — `RenameAttributes`

Renomme des attributs : ancien nom, nouveau nom.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Les renommages s'appliquent ensemble (on peut échanger deux noms). Le nouveau nom ne doit pas déjà exister.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="renameattributes-renamings"></a>Renommages |  |  |

<a id="renameattributesbypattern"></a>

### Renommer en masse — `RenameAttributesByPattern`

Renomme tous les attributs (ou une sélection) : préfixe, suffixe, remplacement, casse, accents.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les opérations s'appliquent dans cet ordre au nom de chaque attribut : remplacement (texte ou expression régulière), retrait des accents, espaces et caractères spéciaux remplacés par _, casse, puis préfixe et suffixe. Sans sélection, tous les attributs sont renommés (la géométrie jamais). Deux attributs qui auraient le même nouveau nom, ou un nom déjà pris par un attribut non renommé, sont refusés. Utile avant une écriture Shapefile (noms de 10 caractères) ou pour normaliser un export.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="renameattributesbypattern-attributes"></a>Attributs | Vide = tous les attributs (hors géométrie). |  |
| <a id="renameattributesbypattern-find"></a>Remplacer | Texte (ou expression régulière) à remplacer dans les noms. |  |
| <a id="renameattributesbypattern-replace"></a>Par | Texte de remplacement (\1 pour un groupe de l'expression). |  |
| <a id="renameattributesbypattern-regex"></a>Expression régulière | « Remplacer » est une expression régulière. | false |
| <a id="renameattributesbypattern-strip_accents"></a>Retirer les accents | é devient e, ç devient c… | false |
| <a id="renameattributesbypattern-clean"></a>Nettoyer | Remplace espaces et caractères spéciaux par _ (« Code INSEE » devient Code_INSEE). | false |
| <a id="renameattributesbypattern-case"></a>Casse | none : inchangée ; lower / upper ; snake : minuscules_avec_tirets_bas (CodeInsee devient code_insee). | `none` |
| <a id="renameattributesbypattern-prefix"></a>Préfixe | Ajouté devant chaque nom. |  |
| <a id="renameattributesbypattern-suffix"></a>Suffixe | Ajouté derrière chaque nom. |  |
| <a id="renameattributesbypattern-max_length"></a>Longueur maximale | Coupe les noms trop longs (0 = pas de limite ; 10 pour un Shapefile). | `0` |

<a id="dropattributes"></a>

### Retirer des attributs — `DropAttributes`

Supprime les attributs choisis ; tous les autres sont conservés.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Un attribut absent est une erreur de configuration (sauf si « Ignorer les absents » est coché).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="dropattributes-attributes"></a>Attributs à retirer | Attributs à supprimer ; vide = rien n'est retiré. |  |
| <a id="dropattributes-remove_geometry"></a>Retirer aussi la géométrie | Retire aussi la géométrie des entités. | false |
| <a id="dropattributes-ignore_missing"></a>Ignorer les attributs absents | Décoché : un attribut absent des données est une erreur ; coché : il est ignoré. | false |

<a id="mapvalues"></a>

### Table de correspondance — `MapValues`

Remplace les valeurs d'un attribut selon une table « valeur d'origine → valeur ».

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La comparaison se fait sur le texte de la valeur (1 et « 1 » sont égaux). Pour les valeurs absentes de la table : « keep » les conserve, « default » met la valeur par défaut, « null » les vide, « reject » envoie l'entité sur « Rejected ». Le résultat est du texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="mapvalues-attribute"></a>Attribut à traduire | Attribut dont les valeurs sont traduites. |  |
| <a id="mapvalues-destination"></a>Attribut résultat | Vide = remplace l'attribut d'origine. |  |
| <a id="mapvalues-mappings"></a>Correspondances | Une ligne par valeur à traduire. Le résultat est du texte. |  |
| <a id="mapvalues-unmatched"></a>Valeur absente de la table | Pour une valeur absente de la table — keep : on la garde ; default : on met la valeur par défaut ; null : on la vide ; reject : l'entité part sur « Rejected ». | `keep` |
| <a id="mapvalues-default"></a>Valeur par défaut | Utilisée quand « Valeur absente de la table » vaut default. |  |
| <a id="mapvalues-ignore_case"></a>Ignorer la casse | Ne distingue pas majuscules et minuscules. | false |

<a id="replacenulls"></a>

### Valeurs vides — `ReplaceNulls`

Remplace les valeurs vides (NULL) par une valeur, ou transforme une valeur donnée en vide.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« fill » : les vides deviennent la valeur choisie (convertie au type de l'attribut ; impossible = erreur de configuration). « to_null » : les valeurs égales à la valeur choisie (ex. « -1 », « N/A ») deviennent vides. « Compter le texte vide » traite aussi « » et les espaces comme des vides. Sans attribut : tous.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="replacenulls-attributes"></a>Attributs | Vide = tous (hors géométrie). |  |
| <a id="replacenulls-mode"></a>Action | fill : remplace les valeurs vides par « Valeur » ; to_null : vide les valeurs égales à « Valeur » (ex. -1, N/A). | `fill` |
| <a id="replacenulls-value"></a>Valeur | fill : valeur écrite à la place des vides ; to_null : valeur à considérer comme vide. |  |
| <a id="replacenulls-blank_is_null"></a>Compter le texte vide comme vide | Traite aussi le texte vide et les espaces comme des valeurs vides. | false |

<a id="explodelist"></a>

### Éclater une liste — `ExplodeList`

Produit une entité par élément d'un attribut de type liste (les autres attributs sont recopiés).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'attribut doit être une liste (ex. issue d'un tableau PostgreSQL ou de « Découper un texte » en mode liste). Une liste vide ou nulle ne produit aucune entité, sauf si « Garder les listes vides » est coché (une entité avec élément vide). « Attribut indice » numérote les éléments à partir de 0.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="explodelist-attribute"></a>Attribut liste | Attribut de type liste à éclater (une entité par élément). |  |
| <a id="explodelist-element"></a>Attribut élément | Vide = remplace l'attribut liste. |  |
| <a id="explodelist-index"></a>Attribut indice | Facultatif. |  |
| <a id="explodelist-keep_empty"></a>Garder les listes vides | Une liste vide ou nulle donne quand même une entité (élément vide) au lieu de disparaître. | false |

## Calculs

<a id="aggregate"></a>

### Agréger — `Aggregate`

Regroupe les entités par clé : une entité par groupe, avec le nombre d'entités et des sommes.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les attributs de l'entité agrégée sont ceux de la première entité du groupe. Géométrie : « collect » réunit les géométries sans les fusionner (MultiPolygon, MultiPoint… ou collection hétérogène), « first » garde celle de la première entité, « none » la retire. Pour fusionner réellement les polygones, utiliser le Dissolve.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="aggregate-group_by"></a>Regrouper par | Vide = tout dans un seul groupe. |  |
| <a id="aggregate-count_attribute"></a>Attribut du nombre d'entités | Vide = pas de comptage. | `_count` |
| <a id="aggregate-sum_attributes"></a>Attributs à additionner | Créent `attribut_sum`. |  |
| <a id="aggregate-geometry"></a>Géométrie | collect : réunit les géométries du groupe sans les fusionner ; first : celle de la première entité ; none : aucune géométrie. | `collect` |

<a id="statisticsbygroup"></a>

### Calculer des statistiques — `StatisticsByGroup`

Calcule des statistiques par groupe : somme, moyenne, min, max, médiane, comptage…

- **Entrées** : `input` — **Sorties** : `output`, `summary` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sortent sur « output » (avec les statistiques de leur groupe si « Ajouter aux entités » est coché) et une ligne par groupe sort sur « summary ». Les colonnes s'appellent `attribut_fonction` (ex. pop_sum) ; `_count` est le nombre d'entités du groupe. Somme, moyenne, médiane et écart-type convertissent la valeur en nombre (non convertible = ignorée). Sans groupe, tout est un seul groupe.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="statisticsbygroup-group_by"></a>Regrouper par | Vide = un seul groupe pour toutes les entités. |  |
| <a id="statisticsbygroup-attributes"></a>Attributs à analyser | Attributs sur lesquels calculer les statistiques (les colonnes s'appellent attribut_fonction, ex. pop_sum). |  |
| <a id="statisticsbygroup-functions"></a>Statistiques | Calculées pour chaque attribut. | `['count', 'sum', 'mean', 'min', 'max']` |
| <a id="statisticsbygroup-count_rows"></a>Compter les entités (_count) | Ajoute _count : le nombre d'entités du groupe. | true |
| <a id="statisticsbygroup-attach"></a>Ajouter aux entités | Recopie les statistiques sur chaque entité. | true |

<a id="pythonscript"></a>

### Code Python — `PythonScript`

Exécute une fonction Python `process(table, ctx)` sur chaque lot d'entités.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Écrire une fonction `process(table, ctx)` : `table` est un lot d'entités (pyarrow.Table ; la géométrie est la colonne `geometry`, en WKB). Elle renvoie une table (sortie « output »), ou un dictionnaire `{"output": t1, "Rejected": t2}` (pour rejeter, `reject(table, "motif")`), ou rien pour ne rien envoyer. Disponibles sans import : `pa` (pyarrow), `pc` (pyarrow.compute), `np`, `shapely`, `duckdb`, `reject`. `ctx` donne `ctx.info("…")`, `ctx.warning("…")` et `ctx.variables`. Le code est appelé lot après lot dans l'ordre d'arrivée ; un état peut se garder dans `ctx.state` (un dictionnaire). **Attention** : ce code s'exécute avec vos droits sur l'ordinateur, comme un plugin : ne l'utiliser que dans des flux de confiance. Le schéma de sortie n'est pas connu avant l'exécution.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="pythonscript-code"></a>Code | Définit `process(table, ctx)`. | `def process(table, ctx):
    """table : un lot d'entités (pyarrow.Table, géométrie WKB dans la colonne "geometry").
    Renvoyer une table, ou {"output": table, "Rejected": autre_table}."""
    return table
` |

<a id="counter"></a>

### Compteur — `Counter`

Ajoute un numéro d'ordre à chaque entité, dans l'ordre d'arrivée (par groupe si demandé).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sans groupe : 1, 2, 3… Avec « Regrouper par » : la numérotation repart de « Début » pour chaque valeur.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="counter-attribute"></a>Attribut créé | Nom de l'attribut qui reçoit le numéro. | `_count` |
| <a id="counter-start"></a>Début | Premier numéro attribué. | `1` |
| <a id="counter-step"></a>Pas | Écart entre deux numéros successifs. | `1` |
| <a id="counter-group_by"></a>Regrouper par | Vide : une seule numérotation ; sinon elle repart de « Début » pour chaque valeur. |  |

<a id="executesql"></a>

### Requête SQL sur les entrées — `ExecuteSql`

Exécute une requête SQL DuckDB sur une ou plusieurs entrées du flux (jointures, agrégats, fenêtres, spatial).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque entrée est une table dont le nom est celui du port : `SELECT p.nom, COUNT(*) AS n FROM input p GROUP BY p.nom`. Plusieurs entrées (liste « Entrées ») permettent des jointures : `SELECT a.*, b.valeur FROM a JOIN b ON a.code = b.code`. La géométrie est la colonne `geometry` (fonctions ST_… de DuckDB : ST_Area, ST_Intersects, ST_Buffer…) et revient dans le résultat avec le système de coordonnées de la première entrée. Une seule instruction SELECT ; ni lecture de fichier ni accès réseau. Le résultat sort sur « output ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="executesql-inputs"></a>Entrées | Noms des ports d'entrée (lettres, chiffres et _) ; chacun est une table de la requête. | `['input']` |
| <a id="executesql-sql"></a>Requête SQL | Une instruction SELECT (ou WITH … SELECT) DuckDB. | `SELECT * FROM input` |

<a id="orderby"></a>

### Trier — `OrderBy`

Trie les entités selon un ou plusieurs attributs (valeurs vides à la fin).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Le tri est stable : à clés égales, l'ordre d'arrivée est conservé. « number » convertit le texte en nombre (une valeur non convertible est une erreur) ; « text » compare les valeurs comme du texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="orderby-keys"></a>Clés de tri | Le premier attribut prime, les suivants départagent les égalités. |  |

## Composants

<a id="componentinput"></a>

### Entrée du composant — `ComponentInput`

Définit une entrée du composant : les entités reçues par ce port arrivent ici.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une entrée du nœud. Pour tester le composant seul, indiquer un fichier d'exemple : il n'est lu que si le flux est lancé directement (jamais quand il sert de composant).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="componentinput-port"></a>Nom du port |  | `input` |
| <a id="componentinput-sample"></a>Fichier d'exemple | Pour tester le composant seul. |  |

<a id="componentoutput"></a>

### Sortie du composant — `ComponentOutput`

Définit une sortie du composant : les entités reçues ici sortent par ce port.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une sortie du nœud. Lancé seul, le nœud laisse simplement passer les entités (utile pour les inspecter).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="componentoutput-port"></a>Nom du port |  | `output` |

## Entrées / sorties

<a id="httprequest"></a>

### Appeler une API web — `HttpRequest`

Appelle une adresse web (GET, POST…) pour chaque entité et range la réponse dans un attribut.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

L'adresse, les en-têtes et le corps peuvent citer des attributs de l'entité avec {attribut} (dans l'adresse, la valeur est encodée). Exemple : https://api-adresse.data.gouv.fr/search/?q={adresse}&limit=1. Les entités dont la réponse a un code inférieur à 400 sortent sur « output » avec le corps de la réponse (attribut `_http_body`) et le code (`_http_status`) ; les autres sortent sur « Rejected » avec le motif (`_rejection_reason`), comme celles dont la réponse dépasse « Taille maximale de la réponse ». Les appels se font en parallèle (« Appels simultanés ») avec un débit maximal par seconde, et sont repris en cas d'erreur réseau, de code 429 ou 5xx. Le proxy du poste (HTTPS_PROXY) est utilisé. Pour lire le JSON reçu : « Extraire du JSON ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="httprequest-url"></a>Adresse | http:// ou https:// ; {attribut} pour une valeur de l'entité. |  |
| <a id="httprequest-method"></a>Méthode | Méthode HTTP. | `GET` |
| <a id="httprequest-headers"></a>En-têtes | En-têtes ajoutés à chaque appel. |  |
| <a id="httprequest-body"></a>Corps | Corps de la requête (POST, PUT, PATCH) ; {attribut} accepté. |  |
| <a id="httprequest-body_attribute"></a>Attribut de la réponse | Reçoit le corps de la réponse (texte). | `_http_body` |
| <a id="httprequest-status_attribute"></a>Attribut du code | Reçoit le code HTTP (vide pour les erreurs réseau). | `_http_status` |
| <a id="httprequest-timeout"></a>Délai (s) | Délai maximal d'un appel. | `30.0` |
| <a id="httprequest-retries"></a>Reprises | Nouvelles tentatives après une erreur réseau, un code 429 ou 5xx. | `2` |
| <a id="httprequest-concurrency"></a>Appels simultanés | Nombre d'appels en parallèle. | `4` |
| <a id="httprequest-rate"></a>Débit maximal (appels/s) | 0 = pas de limite. | `0.0` |
| <a id="httprequest-max_size"></a>Taille maximale de la réponse (Mo) | Au-delà, l'entité part sur « Rejected » : la réponse n'est pas gardée tronquée. | `50.0` |

<a id="createfeatures"></a>

### Créer des entités — `CreateFeatures`

Crée une ou plusieurs entités de toutes pièces : attributs calculés et géométrie facultative.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Point de départ d'un flux qui n'a pas de fichier à lire : une seule entité pour déclencher des nœuds en aval (DatabaseQuery, HttpRequest, RunFlow…) ou plusieurs entités de test. Chaque attribut est une expression SQL qui peut utiliser `_index` (0, 1, 2…) ; les expressions suivantes voient les précédentes. Géométrie : « none », « point » (X et Y) ou « wkt » (texte WKT, ex. POLYGON((0 0, 1 0, 1 1, 0 0))). Le système de coordonnées s'applique à la géométrie.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="createfeatures-count"></a>Nombre d'entités | Combien d'entités créer. | `1` |
| <a id="createfeatures-attributes"></a>Attributs | Calculés dans l'ordre, pour chaque entité. |  |
| <a id="createfeatures-geometry"></a>Géométrie | none, point ou wkt. | `none` |
| <a id="createfeatures-x"></a>X (point) | Expression SQL de X, ex. 1.5 ou 600000 + "_index". | `0` |
| <a id="createfeatures-y"></a>Y (point) | Expression SQL de Y. | `0` |
| <a id="createfeatures-wkt"></a>WKT | Géométrie WKT, identique pour toutes les entités. |  |
| <a id="createfeatures-crs"></a>Système de coordonnées | Celui de la géométrie. |  |

<a id="databasequery"></a>

### SQL (créateur) — `DatabaseQuery`

Exécute un ordre SQL sur une base (PostgreSQL, Oracle) et envoie les lignes qu'il renvoie.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le SQL est celui de la base de la connexion. Par défaut il est exécuté en **lecture seule** (SELECT). Cocher « Autoriser l'écriture » pour un UPDATE, un CREATE TABLE… (validé à la fin, annulé en cas d'erreur) ; un RETURNING (PostgreSQL) renvoie des lignes. Pour renvoyer une géométrie, la produire en WKB sous le nom « geometry » : ST_AsBinary(geom) AS geometry (PostGIS), SDO_UTIL.TO_WKBGEOMETRY(geom) AS "geometry" (Oracle) ; son système de coordonnées se règle dans « Système de coordonnées ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="databasequery-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="databasequery-sql"></a>SQL | Un seul ordre. |  |
| <a id="databasequery-allow_write"></a>Autoriser l'écriture | Décoché : lecture seule (SELECT). Coché : UPDATE, CREATE TABLE… autorisés, validés à la fin. | false |
| <a id="databasequery-crs"></a>Système de coordonnées | Si le résultat contient « geometry ». |  |

## Filtres

<a id="splitbygeometrytype"></a>

### Filtre par type de géométrie — `SplitByGeometryType`

Sépare les entités par type de géométrie : points, lignes, surfaces, collections, sans géométrie.

- **Entrées** : `input` — **Sorties** : `Point`, `Line`, `Area`, `Collection`, `NoGeometry` — **Traitement** : au fil de l'eau

Les multi-géométries suivent leur famille (MultiPolygon → Area). Une géométrie vide compte comme « NoGeometry ».

<a id="extractbylocation"></a>

### Filtre spatial — `ExtractByLocation`

Sépare les entités « candidate » selon leur relation spatiale avec les entités « filter ».

- **Entrées** : `candidate`, `filter` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Une entité candidate sort sur « Passed » si elle vérifie la relation (intersecte, est dans, contient, touche, à moins de N unités…) avec **au moins une** entité « filter », sinon sur « Failed ». Les entités sans géométrie partent sur « Rejected ». Les deux entrées doivent avoir le même système de coordonnées. « Inverser » échange Passed et Failed.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="extractbylocation-predicate"></a>Relation | candidate <relation> filter (« within » : candidate est dans filter). | `intersects` |
| <a id="extractbylocation-distance"></a>Distance (dwithin) | Unités du système de coordonnées. | `0.0` |
| <a id="extractbylocation-invert"></a>Inverser Passed et Failed | Échange les sorties Passed et Failed. | false |

<a id="routebyexpression"></a>

### Filtre à plusieurs sorties — `RouteByExpression`

Répartit les entités sur plusieurs sorties selon des conditions ordonnées.

- **Entrées** : `input` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne donne une sortie et sa condition (expression SQL booléenne). Avec « first », une entité part sur la sortie de la **première** condition qu'elle vérifie ; avec « all », sur toutes celles qu'elle vérifie (elle est alors dupliquée). Celles qui ne vérifient aucune condition partent sur « Failed ». Une condition nulle compte comme fausse ; une entité dont l'évaluation échoue part sur « Rejected » avec le motif. Les sorties du nœud suivent la liste des conditions.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="routebyexpression-tests"></a>Conditions | Une sortie par ligne, dans l'ordre d'évaluation. | `[{'output': 'Passed', 'condition': ''}]` |
| <a id="routebyexpression-mode"></a>Entité vérifiant plusieurs conditions | first : première sortie seulement ; all : toutes les sorties (l'entité est dupliquée). | `first` |

<a id="findduplicates"></a>

### Filtrer les doublons — `FindDuplicates`

Envoie la première entité de chaque clé sur « Unique », les suivantes sur « Duplicate ».

- **Entrées** : `input` — **Sorties** : `Unique`, `Duplicate` — **Traitement** : au fil de l'eau

Deux entités sont des doublons si toutes les clés sont égales (deux valeurs vides sont égales). Sans clé, on compare tous les attributs (hors géométrie). L'ordre d'arrivée décide de la première.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="findduplicates-keys"></a>Clés | Vide = tous les attributs hors géométrie. |  |

<a id="extractbyexpression"></a>

### Filtrer par expression — `ExtractByExpression`

Envoie les entités sur « Passed » si elles vérifient les conditions, sinon sur « Failed ».

- **Entrées** : `input` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : au fil de l'eau

Chaque condition est une expression SQL booléenne : "pop" >= 1000 ; "nom" LIKE 'Saint%' ; "code" IN ('31', '09') ; ST_Area(geometry) > 500. Une condition nulle compte comme fausse. Une entité dont l'évaluation échoue part sur « Rejected » avec le motif.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="extractbyexpression-conditions"></a>Conditions | Expressions SQL booléennes, ex. "pop" >= 1000. |  |
| <a id="extractbyexpression-combine"></a>Combiner par | AND : toutes les conditions ; OR : au moins une. | `AND` |

<a id="sample"></a>

### Échantillonner — `Sample`

Prélève un échantillon : les N premières, les N dernières, une entité sur N ou N entités au hasard.

- **Entrées** : `input` — **Sorties** : `Sampled`, `Remaining` — **Traitement** : bloquant (lit toutes les données avant de produire)

« first » : les N premières entités reçues. « last » : les N dernières. « every » : une entité sur N (la première, puis la N+1-ième…). « random » : N entités tirées au hasard (graine fixe : le tirage se reproduit). Les entités retenues sortent sur « Sampled », les autres sur « Remaining », dans l'ordre d'arrivée. « first » et « every » travaillent au fil de l'eau ; « last » et « random » doivent lire toutes les entités.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="sample-mode"></a>Méthode | first, last, every (une sur N) ou random (au hasard). | `first` |
| <a id="sample-count"></a>N | Nombre d'entités (first, last, random) ou pas (every). | `100` |
| <a id="sample-seed"></a>Graine (random) | Même graine, même tirage. | `0` |

## Géométrie

<a id="geometryfromattribute"></a>

### Attribut vers géométrie — `GeometryFromAttribute`

Construit la géométrie de chaque entité à partir d'un attribut texte (WKT, GeoJSON, WKB hexadécimal).

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Une valeur vide donne une entité sans géométrie ; une valeur illisible envoie l'entité sur « Rejected » avec le motif. Le système de coordonnées est celui indiqué (ou celui des données). Pour des coordonnées X et Y dans deux attributs : « Créer un point / un sommet ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geometryfromattribute-attribute"></a>Attribut contenant la géométrie | Attribut texte qui contient la géométrie. |  |
| <a id="geometryfromattribute-format"></a>Format | Format du texte : wkt, geojson ou wkb_hex (WKB en hexadécimal). | `wkt` |
| <a id="geometryfromattribute-crs"></a>Système de coordonnées | Système de coordonnées de la géométrie lue ; vide = celui des données. |  |
| <a id="geometryfromattribute-remove_attribute"></a>Retirer l'attribut texte | Retire l'attribut texte une fois la géométrie créée. | false |

<a id="creategrid"></a>

### Carroyage — `CreateGrid`

Génère une grille de carrés ou d'hexagones sur une emprise (saisie, ou celle des entités reçues).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'emprise est saisie (X min, Y min, X max, Y max, dans le système de coordonnées choisi) ou, avec « Emprise des entités reçues », prise sur les géométries de l'entrée (le nœud a alors une entrée, et la grille reprend leur système de coordonnées). « Taille » est la largeur d'une cellule, dans l'unité du système (mètres en Lambert-93) : pour un hexagone, la distance entre deux côtés opposés. Chaque cellule reçoit son numéro (`cell_id`), sa colonne et sa ligne. Les hexagones débordent légèrement de l'emprise ; « Ne garder que les cellules qui touchent les entités » écarte les autres. Au plus 5 millions de cellules.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="creategrid-from_input"></a>Emprise des entités reçues | Prend l'emprise sur les géométries de l'entrée. | false |
| <a id="creategrid-xmin"></a>X min | Bord gauche (emprise saisie). | `0.0` |
| <a id="creategrid-ymin"></a>Y min | Bord bas. | `0.0` |
| <a id="creategrid-xmax"></a>X max | Bord droit. | `1000.0` |
| <a id="creategrid-ymax"></a>Y max | Bord haut. | `1000.0` |
| <a id="creategrid-size"></a>Taille | Largeur d'une cellule, dans l'unité du système de coordonnées. | `100.0` |
| <a id="creategrid-shape"></a>Forme | square ou hexagon. | `square` |
| <a id="creategrid-crs"></a>Système de coordonnées | Celui de l'emprise saisie. |  |
| <a id="creategrid-only_touching"></a>Ne garder que les cellules qui touchent les entités | Avec l'emprise des entités : écarte les cellules qui n'en rencontrent aucune. | false |

<a id="pointfromxy"></a>

### Créer un point / un sommet — `PointFromXY`

Crée un point depuis des attributs X/Y (Z), ou ajoute ce point comme sommet d'une ligne.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« point » : la géométrie devient le point (X, Y[, Z]). « append » : le point est ajouté à la fin de la ligne existante (une entité sans géométrie reçoit un simple point). Une coordonnée vide ou non numérique envoie l'entité sur « Rejected ». Le système de coordonnées est celui indiqué (ou celui des données).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="pointfromxy-x"></a>Attribut X (ou longitude) | Attribut contenant X (ou la longitude). |  |
| <a id="pointfromxy-y"></a>Attribut Y (ou latitude) | Attribut contenant Y (ou la latitude). |  |
| <a id="pointfromxy-z"></a>Attribut Z | Facultatif. |  |
| <a id="pointfromxy-mode"></a>Action | point : la géométrie devient le point ; append : le point est ajouté à la fin de la ligne existante. | `point` |
| <a id="pointfromxy-crs"></a>Système de coordonnées | Vide = celui des données. |  |

<a id="clip"></a>

### Découper — `Clip`

Découpe les entités « input » par les polygones « overlay » : parties dedans et dehors.

- **Entrées** : `input`, `overlay` — **Sorties** : `Inside`, `Outside`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

« union » : les polygones de découpe sont fusionnés ; chaque entité donne au plus une partie « Inside » (dans les polygones de découpe) et une partie « Outside ». « each » : une partie « Inside » par polygone de découpe qui la recouvre, avec les attributs choisis du polygone (préfixés) : c'est une intersection avec attributs. Les parties vides sont supprimées, comme les simples contacts (une parcelle qui touche le polygone de découpe par un bord reste entière dehors). Une entité entièrement dedans ou dehors sort telle quelle, sans calcul : seules les entités à cheval sont découpées. Les entités sans géométrie, ou que la découpe n'a pas pu traiter, partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="clip-mode"></a>Polygones de découpe | union : les polygones de découpe sont fusionnés (au plus une partie Inside par entité) ; each : une partie Inside par polygone de découpe qui recouvre l'entité. | `union` |
| <a id="clip-overlay_attributes"></a>Attributs des polygones à copier | Mode « each ». |  |
| <a id="clip-prefix"></a>Préfixe des attributs copiés | Ajouté devant le nom des attributs copiés du polygone de découpe (mode each). | `overlay_` |

<a id="hull"></a>

### Enveloppe des géométries — `Hull`

Remplace la géométrie par son enveloppe : convexe, concave, emprise, rectangle orienté ou cercle englobant.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« convex » : enveloppe convexe (l'élastique autour des points). « concave » : enveloppe qui épouse mieux la forme ; « Finesse » va de 1 (convexe) à 0 (très creusée). « envelope » : emprise (rectangle aligné sur les axes). « rectangle » : rectangle orienté de plus petite surface. « circle » : plus petit cercle englobant. Portée : « feature » calcule l'enveloppe de chaque entité ; « group » réunit les entités de chaque groupe en une seule (attributs de la première) ; « all » en fait une seule pour tout. Les entités sans géométrie partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="hull-method"></a>Enveloppe | convex, concave, envelope, rectangle ou circle. | `convex` |
| <a id="hull-scope"></a>Portée | feature : une par entité ; group : une par groupe ; all : une pour toutes les entités. | `feature` |
| <a id="hull-group_by"></a>Regrouper par | Pour la portée « group ». |  |
| <a id="hull-ratio"></a>Finesse (concave) | 1 = convexe, 0 = très creusée. | `0.3` |
| <a id="hull-allow_holes"></a>Trous autorisés (concave) | L'enveloppe concave peut avoir des trous. | false |

<a id="extractcoordinates"></a>

### Extraire des coordonnées — `ExtractCoordinates`

Écrit X, Y (et Z) d'un point caractéristique de la géométrie dans des attributs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Point utilisé : le point lui-même, le centre de gravité, un point garanti à l'intérieur de la surface, le premier ou le dernier sommet, ou le centre de l'emprise. Les coordonnées sont celles du système de la géométrie. « Emprise » ajoute `préfixe_minx`, `_miny`, `_maxx`, `_maxy`.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="extractcoordinates-source"></a>Point | Quel point lire : point (le point lui-même), centroid (centre de gravité), point_on_surface (toujours dedans), start / end (premier / dernier sommet), bbox_center (centre de l'emprise). | `centroid` |
| <a id="extractcoordinates-prefix"></a>Préfixe des attributs | Ex. « pt_ » donne pt_x, pt_y. |  |
| <a id="extractcoordinates-z"></a>Extraire aussi Z | Ajoute aussi l'altitude (z). | false |
| <a id="extractcoordinates-bounds"></a>Ajouter l'emprise | Ajoute l'emprise : minx, miny, maxx, maxy (avec le préfixe). | false |

<a id="dissolve"></a>

### Fusionner (dissoudre) — `Dissolve`

Fusionne les polygones (ou lignes) de chaque groupe en une seule géométrie, sans frontières internes.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Une entité par groupe, avec les attributs de la première entité du groupe. Les polygones qui se touchent sont fusionnés ; les lignes sont réunies puis jointes bout à bout quand c'est possible. Les entités sans géométrie sont ignorées du calcul. Différent d'Aggregate, qui réunit sans fusionner.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="dissolve-group_by"></a>Regrouper par | Vide = tout fusionner. |  |
| <a id="dissolve-count_attribute"></a>Attribut du nombre d'entités | Vide = pas de comptage. | `_count` |

<a id="geometrytoattribute"></a>

### Géométrie vers attribut — `GeometryToAttribute`

Copie la géométrie de chaque entité dans un attribut texte (WKT, GeoJSON ou WKB hexadécimal).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Utile pour écrire une géométrie dans un CSV ou une colonne texte. « Retirer la géométrie » enlève ensuite la géométrie de l'entité. Une entité sans géométrie reçoit un attribut vide. Précision des coordonnées : laisser vide pour la précision complète.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geometrytoattribute-attribute"></a>Attribut créé | Nom de l'attribut texte qui reçoit la géométrie. | `geometry_text` |
| <a id="geometrytoattribute-format"></a>Format | wkt, geojson ou wkb_hex (WKB en hexadécimal). | `wkt` |
| <a id="geometrytoattribute-precision"></a>Décimales (WKT, GeoJSON) | Décimales conservées dans les coordonnées ; vide = précision complète. |  |
| <a id="geometrytoattribute-remove_geometry"></a>Retirer la géométrie | Retire la géométrie de l'entité une fois copiée dans l'attribut. | false |

<a id="measurelength"></a>

### Longueur de lignes — `MeasureLength`

Ajoute la longueur de chaque ligne.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système (les coordonnées doivent alors être en longitude/latitude). Les entités qui ne sont pas des lignes (points, polygones, sans géométrie) partent sur « Rejected » ; pour le périmètre d'un polygone, utiliser « Surface et périmètre ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="measurelength-attribute"></a>Attribut longueur | Reçoit la longueur. | `_length` |
| <a id="measurelength-unit"></a>Unité | Mètres ou kilomètres. | `m` |
| <a id="measurelength-method"></a>Méthode | planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur l'ellipsoïde WGS84, coordonnées en longitude/latitude. | `planar` |
| <a id="measurelength-decimals"></a>Décimales | Nombre de décimales conservées (0 à 15). | `2` |

<a id="centroids"></a>

### Remplacer par un point central — `Centroids`

Remplace chaque géométrie par un point : centre de gravité, point intérieur ou centre de l'emprise.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« centroid » : centre de gravité (peut tomber hors d'un polygone en forme de croissant) ; « point_on_surface » : un point toujours à l'intérieur ; « bbox_center » : centre de l'emprise. Les entités sans géométrie ou à géométrie vide partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="centroids-method"></a>Méthode | centroid : centre de gravité (peut sortir d'un polygone en croissant) ; point_on_surface : un point toujours à l'intérieur ; bbox_center : centre de l'emprise. | `centroid` |

<a id="reproject"></a>

### Reprojeter — `Reproject`

Transforme les géométries dans un autre système de coordonnées.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Le système d'origine est celui des données ; on peut le forcer si les données n'en déclarent pas (ou un faux). Les entités sans géométrie passent telles quelles ; une géométrie dont la transformation échoue (hors du domaine de validité, valeurs infinies) part sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="reproject-target"></a>Système cible | Ex. EPSG:2154. |  |
| <a id="reproject-source"></a>Système source | Vide = celui des données. |  |

<a id="fixgeometries"></a>

### Réparer les géométries — `FixGeometries`

Rend valides les géométries invalides (auto-intersection, anneau non fermé…) ; les autres sont intactes.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Toutes les entités ressortent sur « output » avec une géométrie valide. Les géométries déjà valides ne sont pas touchées. Les autres sont corrigées (GEOS make_valid) : un polygone en « nœud papillon » devient un multipolygone de deux triangles, un anneau mal fermé est refermé. « Garder le type » écarte les morceaux dégénérés que la réparation peut produire (un segment ou un point né d'un polygone aplati) : le polygone reste un polygone. « Rejected » reçoit, avec le motif, les entités sans géométrie, à géométrie vide ou qui ne donnent plus rien une fois réparées. Pour seulement contrôler sans modifier, utiliser « Valider les géométries ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="fixgeometries-method"></a>Méthode | linework : les lignes sont découpées aux intersections (comportement classique, rapide). structure : reconstruit les surfaces d'après leurs anneaux (trous mieux respectés, plus lent). | `linework` |
| <a id="fixgeometries-keep_type"></a>Garder le type de géométrie | Écarte les morceaux de dimension inférieure (lignes ou points nés d'un polygone aplati). | true |
| <a id="fixgeometries-report_attribute"></a>Attribut du motif de réparation | Reçoit le défaut d'origine pour les géométries réparées, vide pour les autres. Vide = pas d'attribut. | `_repaired_reason` |

<a id="simplify"></a>

### Simplifier les géométries — `Simplify`

Réduit le nombre de sommets en gardant la forme à la tolérance près (Douglas-Peucker).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

La tolérance est dans l'unité du système de coordonnées. « Préserver la topologie » évite les auto-intersections et les polygones qui disparaissent (un peu plus lent).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="simplify-tolerance"></a>Tolérance | Écart maximal toléré, dans l'unité du système de coordonnées (mètres en Lambert-93). | `1.0` |
| <a id="simplify-preserve_topology"></a>Préserver la topologie | Évite les auto-intersections et les polygones qui disparaissent (un peu plus lent). | true |

<a id="overlaypolygons"></a>

### Superposition de polygones — `OverlayPolygons`

Découpe des polygones qui se chevauchent en morceaux disjoints, avec le nombre de polygones qui les recouvrent.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Tous les polygones reçus sont superposés : leurs contours découpent le plan en morceaux, et chaque morceau sort sur « output » avec `_overlap_count` (le nombre de polygones d'origine qui le recouvrent), `_origins` (leurs numéros, de 0, dans l'ordre d'arrivée) et, pour chaque attribut de « Attributs à lister », la liste des valeurs des polygones qui le recouvrent. Les morceaux qu'aucun polygone ne recouvre (trous, vide autour) ne sont pas produits. « Recouvrement minimal » ne garde que les morceaux recouverts au moins N fois (2 = les zones de conflit). Les entités qui ne sont pas des polygones partent sur « Rejected ». Toutes les entités sont lues avant le calcul.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="overlaypolygons-attributes"></a>Attributs à lister | Pour chaque morceau : la liste des valeurs de ces attributs sur les polygones qui le recouvrent. |  |
| <a id="overlaypolygons-min_overlap"></a>Recouvrement minimal | Ne garde que les morceaux recouverts au moins ce nombre de fois. | `1` |
| <a id="overlaypolygons-count_attribute"></a>Attribut du nombre | Nombre de polygones qui recouvrent le morceau. | `_overlap_count` |
| <a id="overlaypolygons-origins_attribute"></a>Attribut des origines | Numéros (de 0) des polygones qui recouvrent le morceau ; vide = aucun. | `_origins` |

<a id="measurearea"></a>

### Surface et périmètre — `MeasureArea`

Ajoute la surface (et au choix le périmètre) de chaque polygone.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système (les coordonnées doivent alors être en longitude/latitude). Les entités qui ne sont pas des polygones partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="measurearea-area"></a>Attribut surface | Vide = pas de surface. | `_area` |
| <a id="measurearea-perimeter"></a>Attribut périmètre | Vide = pas de périmètre. |  |
| <a id="measurearea-area_unit"></a>Unité de surface | Unité de la surface écrite : m² (m2), hectares (ha) ou km² (km2). | `m2` |
| <a id="measurearea-length_unit"></a>Unité de périmètre | Unité du périmètre écrit : mètres ou kilomètres. | `m` |
| <a id="measurearea-method"></a>Méthode | planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur l'ellipsoïde WGS84, les coordonnées doivent être en longitude/latitude. | `planar` |
| <a id="measurearea-decimals"></a>Décimales | Nombre de décimales conservées (0 à 15). | `2` |

<a id="checkvalidity"></a>

### Valider les géométries — `CheckValidity`

Sépare les géométries valides des invalides (auto-intersection, anneau non fermé…).

- **Entrées** : `input` — **Sorties** : `Valid`, `Invalid` — **Traitement** : au fil de l'eau

Les entités valides sortent sur « Valid », les autres sur « Invalid » avec le motif dans `_invalid_reason`. Géométrie vide ou absente : « Invalid » (sauf si décoché). Pour corriger les géométries invalides, utiliser « Réparer les géométries ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="checkvalidity-reject_empty"></a>Refuser les géométries vides ou absentes | Coché : une géométrie vide ou absente va sur « Invalid » ; décoché : elle passe sur « Valid ». | true |

<a id="buffer"></a>

### Zone tampon — `Buffer`

Remplace chaque géométrie par une zone tampon (buffer) de la distance indiquée.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La distance est dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). Elle peut venir d'un attribut. Une distance négative rétrécit un polygone (résultat vide = entité sur « Rejected »). Les entités sans géométrie ou à distance invalide partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="buffer-distance"></a>Distance | Dans l'unité du système de coordonnées (mètres en Lambert-93). Négative : rétrécit un polygone. | `10.0` |
| <a id="buffer-distance_attribute"></a>Distance depuis un attribut | Remplace la distance fixe. |  |
| <a id="buffer-end_cap"></a>Extrémités | Forme du bout des lignes : round (arrondi), flat (plat), square (carré). | `round` |
| <a id="buffer-join"></a>Angles | Forme des angles : round (arrondi), mitre (angle vif), bevel (coupé). | `round` |
| <a id="buffer-quad_segs"></a>Segments par quart de cercle | Finesse des arrondis : segments par quart de cercle. | `16` |

<a id="multiparttosingleparts"></a>

### Éclater les géométries multiples — `MultipartToSingleparts`

Sépare chaque géométrie multiple (MultiPolygon, MultiLineString…) en une entité par partie.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Une entité dont la géométrie a N parties donne N entités qui recopient les autres attributs. Les géométries simples, vides ou absentes passent telles quelles. « Éclater aussi les collections imbriquées » descend dans les GeometryCollection qui contiennent d'autres collections. « Attribut du numéro » reçoit le rang de la partie (à partir de 0) et « Attribut du nombre » le nombre de parties de l'entité d'origine.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="multiparttosingleparts-part_attribute"></a>Attribut du numéro | Rang de la partie, de 0 ; vide = aucun. | `_part` |
| <a id="multiparttosingleparts-count_attribute"></a>Attribut du nombre | Nombre de parties de l'entité d'origine ; vide = aucun. |  |
| <a id="multiparttosingleparts-recursive"></a>Éclater aussi les collections imbriquées | Descend dans les collections de collections. | true |

## Jointures

<a id="joinbyfield"></a>

### Jointure attributaire — `JoinByField`

Ajoute aux entités de « base » les attributs des entités de « join » qui ont la même clé.

- **Entrées** : `base`, `join` — **Sorties** : `Joined`, `UnjoinedBase`, `UnjoinedJoin` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités de « base » avec correspondance sortent sur « Joined » ; les autres sur « UnjoinedBase ». Les entités de « join » qui ne correspondent à aucune entité de « base » sortent sur « UnjoinedJoin ». Une clé nulle ne correspond jamais. Si les clés n'ont pas le même type (entier et texte), la comparaison se fait en texte. La géométrie du résultat est celle de « base », celle de « join » ou aucune, au choix.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="joinbyfield-base_keys"></a>Clés de « base » | Attributs de « base » qui font le lien avec « join », dans le même ordre que les clés de « join ». |  |
| <a id="joinbyfield-join_keys"></a>Clés de « join » | Dans le même ordre que « base ». |  |
| <a id="joinbyfield-attributes"></a>Attributs à ajouter | Vide = tous ceux de « join » hors clés. |  |
| <a id="joinbyfield-prefix"></a>Préfixe des attributs ajoutés | Évite les conflits de noms. |  |
| <a id="joinbyfield-conflict"></a>Si le nom existe déjà | other : la valeur de « join » ; base : on garde l'existante. | `other` |
| <a id="joinbyfield-on_multiple"></a>Plusieurs correspondances | all : une entité par correspondance ; first ; error. | `all` |
| <a id="joinbyfield-keep_unjoined"></a>Garder les non-appariées dans Joined | Jointure « gauche » : attributs vides. | false |
| <a id="joinbyfield-geometry"></a>Géométrie du résultat | Celle de « base », celle de « join » (vide pour une entité gardée sans correspondance), ou aucune. | `base` |

<a id="joinbynearest"></a>

### Plus proches voisines — `JoinByNearest`

Pour chaque entité « base », trouve ses k plus proches entités parmi les « candidate » et recopie leurs attributs.

- **Entrées** : `base`, `candidate` — **Sorties** : `Matched`, `NoNeighbor`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque « base » donne au plus k entités sur « Matched » (une par voisine, de la plus proche à la plus éloignée) : les attributs de la base, ceux de la voisine (préfixés), son rang (`_rank`, de 1) et la distance (`_distance`, dans l'unité du système de coordonnées). Une base sans voisine à moins de « Distance maximale » sort sur « NoNeighbor ». La distance est celle qui sépare les géométries (le bord à bord pour des polygones), pas celle des centres. Les deux entrées doivent avoir le même système de coordonnées ; une entité sans géométrie part sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="joinbynearest-count"></a>Nombre de voisines (k) | Combien de plus proches voisines chercher. | `1` |
| <a id="joinbynearest-max_distance"></a>Distance maximale | Dans l'unité du système de coordonnées ; 0 = sans limite. | `0.0` |
| <a id="joinbynearest-attributes"></a>Attributs de la voisine | Vide = tous ceux de « candidate » hors géométrie. |  |
| <a id="joinbynearest-prefix"></a>Préfixe | Ajouté devant les attributs de la voisine. | `near_` |
| <a id="joinbynearest-rank_attribute"></a>Attribut du rang | Rang de la voisine, de 1. | `_rank` |
| <a id="joinbynearest-distance_attribute"></a>Attribut de la distance | Distance à la voisine. | `_distance` |

<a id="pointsinpolygons"></a>

### Point dans polygone — `PointsInPolygons`

Ajoute à chaque point les attributs du polygone qui le contient (jointure spatiale).

- **Entrées** : `points`, `areas` — **Sorties** : `Inside`, `Outside`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les points dans un polygone sortent sur « Inside » avec les attributs choisis du polygone (tous par défaut), les autres sur « Outside ». Si plusieurs polygones contiennent le point : « first » garde le premier, « all » produit une entité par polygone, « error » refuse. Un point sur la frontière compte comme dedans. Les entités sans géométrie partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="pointsinpolygons-attributes"></a>Attributs du polygone à copier | Vide = tous hors géométrie. |  |
| <a id="pointsinpolygons-prefix"></a>Préfixe des attributs copiés | Ajouté devant le nom des attributs copiés du polygone. |  |
| <a id="pointsinpolygons-on_multiple"></a>Plusieurs polygones | Si plusieurs polygones contiennent le point — first : le premier ; all : une entité par polygone ; error : refus. | `first` |

<a id="joinbylocation"></a>

### Relateur spatial — `JoinByLocation`

Ajoute à chaque entité « base » le nombre d'entités « related » en relation avec elle, et au choix la somme ou la liste de leurs attributs, ou leurs attributs eux-mêmes.

- **Entrées** : `base`, `related` — **Sorties** : `output`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Pour chaque entité de « base », on compte les entités de « related » qui vérifient la relation (base <relation> related), 0 s'il n'y en a aucune. Options : additionner des attributs numériques des entités liées (« <attribut>_sum ») ; lister les valeurs d'un attribut (séparées par un séparateur, sans doublon, triées) ; récupérer des attributs des entités liées, comme une jointure attributaire (préfixe, conflit de noms) : ceux de la première entité liée, une entité « base » par entité liée, ou la liste des valeurs de chaque attribut ; vides sans entité liée. Compter des points dans des polygones : relation intersects, polygones sur « base », points sur « related » ; un point sur une limite commune compte dans les deux polygones, ou seulement dans le premier avec « first ». Sans liste, les entités « related » passent lot par lot sans être gardées en mémoire : des millions de points se comptent sans peine. Les entités « base » sans géométrie partent sur « Rejected » ; les entités « related » sans géométrie ne comptent nulle part.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="joinbylocation-predicate"></a>Relation | base <relation> related. | `intersects` |
| <a id="joinbylocation-distance"></a>Distance (dwithin) | Distance maximale pour la relation dwithin, dans l'unité du système de coordonnées. | `0.0` |
| <a id="joinbylocation-count_attribute"></a>Attribut du nombre d'entités liées | Nom de l'attribut qui reçoit le nombre d'entités de « related » vérifiant la relation. | `_related_count` |
| <a id="joinbylocation-sum_attributes"></a>Attributs à additionner | Attributs numériques de « related » : leur somme par entité « base » dans « <attribut>_sum ». |  |
| <a id="joinbylocation-on_multiple"></a>Entité liée à plusieurs entités « base » | all : comptée pour chacune ; first : pour la première seulement (la somme des comptes égale alors le nombre d'entités liées). | `all` |
| <a id="joinbylocation-list_attribute"></a>Lister cet attribut des entités liées | Vide = pas de liste. |  |
| <a id="joinbylocation-list_name"></a>Nom de l'attribut de liste | Nom de l'attribut qui reçoit la liste (si « Lister cet attribut » est renseigné). | `_related_values` |
| <a id="joinbylocation-separator"></a>Séparateur | Texte placé entre deux valeurs de la liste. | `, ` |
| <a id="joinbylocation-attributes"></a>Attributs à récupérer | Attributs de « related » ajoutés à l'entité « base », comme une jointure attributaire. Vide = aucun. |  |
| <a id="joinbylocation-merge"></a>Plusieurs entités liées | Pour les attributs récupérés. first : ceux de la première entité liée (ordre de « related ») ; all : une entité « base » par entité liée ; list : les valeurs distinctes de chaque attribut, jointes par le séparateur. | `first` |
| <a id="joinbylocation-prefix"></a>Préfixe des attributs récupérés | Évite les conflits de noms. |  |
| <a id="joinbylocation-conflict"></a>Si le nom existe déjà | other : la valeur de « related » ; base : on garde celle de « base ». | `other` |

<a id="databasequeryperfeature"></a>

### SQL (par entité) — `DatabaseQueryPerFeature`

Exécute un ordre SQL pour chaque entité, en y injectant ses attributs ; ajoute le résultat.

- **Entrées** : `input` — **Sorties** : `output`, `NoResult` — **Traitement** : au fil de l'eau

Écrire `{{attribut}}` dans le SQL : la valeur de l'entité est transmise comme **paramètre lié** (jamais collée dans le texte : pas d'injection SQL). Ex. SELECT nom FROM communes WHERE code = {{insee}}. « first » ajoute les colonnes de la première ligne du résultat ; « all » produit une entité par ligne. Une entité sans résultat part sur « NoResult ». Lecture seule sauf « Autoriser l'écriture » (tout est validé à la fin, annulé en cas d'erreur). Une requête par entité : prévoir de la jointure SQL si le volume est important.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="databasequeryperfeature-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="databasequeryperfeature-sql"></a>SQL | Avec {{attribut}} pour les valeurs de l'entité. |  |
| <a id="databasequeryperfeature-rows"></a>Lignes du résultat | first : les colonnes de la première ligne du résultat sont ajoutées à l'entité ; all : une entité par ligne du résultat. | `first` |
| <a id="databasequeryperfeature-prefix"></a>Préfixe des colonnes ajoutées | Ajouté devant le nom des colonnes ajoutées. |  |
| <a id="databasequeryperfeature-allow_write"></a>Autoriser l'écriture | Décoché : lecture seule (SELECT). Coché : UPDATE, INSERT… autorisés, validés à la fin. | false |

## Lecture

<a id="csvreader"></a>

### Lecture CSV — `CsvReader`

Lit un fichier CSV (ou texte délimité) ; une colonne WKT ou des colonnes X/Y donnent la géométrie.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les colonnes sont lues comme du texte (les codes à zéro initial sont conservés), sauf si « Détecter les types » est coché. Indiquer la colonne WKT, ou les colonnes X et Y, pour obtenir une géométrie, avec son système de coordonnées. Un motif (dossier/*.csv) lit tous les fichiers : leurs colonnes sont réunies par nom.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="csvreader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="csvreader-delimiter"></a>Séparateur | Vide = détection automatique. |  |
| <a id="csvreader-encoding"></a>Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| <a id="csvreader-header"></a>Première ligne = en-têtes | Décocher si le fichier n'a pas de ligne d'en-têtes. | true |
| <a id="csvreader-detect_types"></a>Détecter les types | Sinon tout est lu en texte : les codes à zéro initial sont conservés. | false |
| <a id="csvreader-wkt_column"></a>Colonne WKT | Colonne contenant la géométrie en WKT. |  |
| <a id="csvreader-x_column"></a>Colonne X | Colonne des X (ou longitudes), avec la colonne Y. |  |
| <a id="csvreader-y_column"></a>Colonne Y | Colonne des Y (ou latitudes), avec la colonne X. |  |
| <a id="csvreader-crs"></a>Système de coordonnées | Celui de la colonne WKT ou des X/Y. |  |
| <a id="csvreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="csvreader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="csvreader-where"></a>Filtre (WHERE) | Condition SQL (DuckDB) appliquée à la lecture : seules les entités qui la vérifient sont lues, et DuckDB ne lit du fichier que les parties utiles ; vide = toutes les entités. |  |
| <a id="csvreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="excelreader"></a>

### Lecture Excel — `ExcelReader`

Lit une feuille d'un classeur Excel (.xlsx).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

La première ligne de la plage donne les noms de colonnes (sauf si décoché). Vide : la première feuille.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="excelreader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="excelreader-sheet"></a>Feuille | Nom de la feuille ; vide = la première. |  |
| <a id="excelreader-cell_range"></a>Plage | Ex. A1:F200 ; vide = toute la feuille. |  |
| <a id="excelreader-header"></a>Première ligne = en-têtes | Décocher si la plage n'a pas de ligne d'en-têtes. | true |
| <a id="excelreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="excelreader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="excelreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="flatgeobufreader"></a>

### Lecture FlatGeobuf — `FlatGeobufReader`

Lit un fichier FlatGeobuf (.fgb).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Format vecteur compact et indexé, rapide à lire.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="flatgeobufreader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="flatgeobufreader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="flatgeobufreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="flatgeobufreader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="flatgeobufreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="geojsonreader"></a>

### Lecture GeoJSON — `GeoJsonReader`

Lit un fichier GeoJSON.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le GeoJSON est en WGS 84 (EPSG:4326) selon la norme ; un autre système déclaré dans le fichier est respecté.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geojsonreader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="geojsonreader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="geojsonreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="geojsonreader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="geojsonreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="geopackagereader"></a>

### Lecture GeoPackage — `GeoPackageReader`

Lit une couche d'un GeoPackage (.gpkg).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Indiquer la couche si le GeoPackage en contient plusieurs (vide : la première).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geopackagereader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="geopackagereader-layer"></a>Couche | Nom de la couche ; vide = la première. |  |
| <a id="geopackagereader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="geopackagereader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="geopackagereader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="geopackagereader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="geoparquetreader"></a>

### Lecture (Geo)Parquet — `GeoParquetReader`

Lit un fichier Parquet, avec ou sans géométrie (GeoParquet), sur le disque ou à une adresse web (https://).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Un Parquet sans géométrie est lu tel quel. Pour un GeoParquet, la géométrie et son système de coordonnées sont lus dans ses métadonnées. Un fichier seul, local, sans nom du fichier ajouté ni limite de lignes, n'est pas recopié : le résultat du nœud est le fichier lui-même (le modifier ensuite oblige à relancer le flux). Une adresse https:// est lue par plages : seules les parties utiles du fichier sont téléchargées (proxy des Paramètres).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geoparquetreader-path"></a>Fichier | Chemin du fichier, motif (*.ext, dossier/**/*.ext : tous les fichiers trouvés) ou adresse web https://. |  |
| <a id="geoparquetreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="geoparquetreader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="geoparquetreader-where"></a>Filtre (WHERE) | Condition SQL (DuckDB) appliquée à la lecture : seules les entités qui la vérifient sont lues, et DuckDB ne lit du fichier que les parties utiles ; vide = toutes les entités. |  |
| <a id="geoparquetreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="mapinforeader"></a>

### Lecture MapInfo — `MapInfoReader`

Lit une table MapInfo (.tab) ou un fichier MIF/MID (.mif).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le format (TAB ou MIF) est déduit de l'extension.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="mapinforeader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="mapinforeader-encoding"></a>Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| <a id="mapinforeader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="mapinforeader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |
| <a id="mapinforeader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="mapinforeader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |

<a id="oraclereader"></a>

### Lecture Oracle — `OracleReader`

Lit une table, une vue ou une requête Oracle ; la colonne SDO_GEOMETRY devient la géométrie du flux.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Choisir une table (schéma puis table) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est exécuté par le serveur. Le système de coordonnées est le SRID des géométries (SDO_SRID). Oracle range en majuscules les noms saisis sans guillemets : les colonnes lues sont en majuscules (NOM, GEOM).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="oraclereader-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="oraclereader-table"></a>Table ou vue | Table ou vue lue en entier. Ignorée si une requête est renseignée. |  |
| <a id="oraclereader-query"></a>Requête SQL | Facultatif : remplace la table ; un seul SELECT. |  |
| <a id="oraclereader-where"></a>Filtre (WHERE) | Condition SQL ajoutée en WHERE, évaluée par le serveur ; vide = aucun filtre. |  |
| <a id="oraclereader-geometry_column"></a>Colonne géométrique | Vide = la première trouvée. |  |
| <a id="oraclereader-crs"></a>Système de coordonnées | Vide = SRID de la colonne. |  |
| <a id="oraclereader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="postgresreader"></a>

### Lecture PostgreSQL / PostGIS — `PostgresReader`

Lit une table, une vue ou une requête PostgreSQL ; la géométrie PostGIS devient la géométrie du flux.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Choisir une table (schéma puis table, dans les listes lues sur la connexion) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est exécuté par le serveur, donc seules les lignes utiles circulent. La lecture se fait en lecture seule, par curseur serveur (mémoire bornée). Le système de coordonnées est celui de la colonne (SRID). Les « numeric » sont lus en flottant ; les types non gérés (intervalles, hstore…) en texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="postgresreader-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="postgresreader-table"></a>Table ou vue | Table ou vue à lire en entier (équivaut à SELECT * FROM table). Ignorée si une requête est renseignée. |  |
| <a id="postgresreader-query"></a>Requête SQL | Facultatif. Vide : la table est lue en entier (SELECT * FROM table). Renseignée : remplace la table ; un seul SELECT (un « ; » final est toléré). |  |
| <a id="postgresreader-where"></a>Filtre (WHERE) | Condition SQL ajoutée en WHERE, évaluée par le serveur ; vide = aucun filtre. |  |
| <a id="postgresreader-geometry_column"></a>Colonne géométrique | Vide = la première trouvée. |  |
| <a id="postgresreader-crs"></a>Système de coordonnées | Vide = SRID de la colonne. |  |
| <a id="postgresreader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="spatialitereader"></a>

### Lecture SQLite / SpatiaLite — `SpatiaLiteReader`

Lit une table d'une base SQLite ou SpatiaLite (.sqlite, .db), ou le résultat d'une requête SQL.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Indiquer la table (vide : la première) **ou** une requête SQL (dialecte SQLite), qui la remplace. La géométrie SpatiaLite devient la géométrie du flux. Pour un GeoPackage, utiliser « Lecture GeoPackage ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="spatialitereader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="spatialitereader-layer"></a>Table | Nom de la table ; vide = la première. |  |
| <a id="spatialitereader-sql"></a>Requête SQL | Facultatif : remplace la table (dialecte SQLite) ; un seul SELECT. |  |
| <a id="spatialitereader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="spatialitereader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="spatialitereader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="spatialitereader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

<a id="shapefilereader"></a>

### Lecture Shapefile — `ShapefileReader`

Lit un Shapefile (.shp et ses fichiers compagnons).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'encodage est lu dans le fichier .cpg s'il existe ; sinon utf-8, puis latin-1.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="shapefilereader-path"></a>Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| <a id="shapefilereader-encoding"></a>Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| <a id="shapefilereader-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| <a id="shapefilereader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| <a id="shapefilereader-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| <a id="shapefilereader-max_rows"></a>Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

## Lots et variables

<a id="runflow"></a>

### Lancer un flux — `RunFlow`

Exécute un autre flux pour chaque entité reçue, avec des paramètres tirés de ses attributs.

- **Entrées** : `input` — **Sorties** : `Succeeded`, `Failed` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque entité reçue lance une exécution du flux indiqué (`etl run` dans un processus séparé : mémoire isolée, un plantage n'arrête pas les autres). Les paramètres publiés du flux lancé reçoivent la valeur d'un attribut de l'entité (ou une valeur fixe). Les entités dont le flux a réussi sortent sur « Succeeded », les autres sur « Failed » ; dans les deux cas elles gardent leurs attributs et reçoivent _flow_status, _flow_seconds et _flow_error. « En parallèle » règle le nombre d'exécutions simultanées. Pour un dossier de fichiers : ListFiles → RunFlow, avec le paramètre du flux lancé relié à l'attribut path.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="runflow-flow"></a>Flux à lancer | Fichier .json du flux. |  |
| <a id="runflow-parameters"></a>Paramètres | Valeur de chaque paramètre publié du flux lancé. |  |
| <a id="runflow-parallel"></a>En parallèle | Exécutions simultanées (1 à 16). | `1` |
| <a id="runflow-stop_on_error"></a>S'arrêter au premier échec | Coché : les exécutions restantes ne sont pas lancées après un échec. | false |
| <a id="runflow-cache"></a>Garder les données | Garde le cache de chaque exécution (consomme du disque). | false |

<a id="readlistedfiles"></a>

### Lire les fichiers listés — `ReadListedFiles`

Lit le fichier désigné par chaque entité reçue et envoie leurs entités.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque entité reçue donne un chemin de fichier (attribut « Chemin ») ; le fichier est lu avec les mêmes réglages que le lecteur de son format et ses entités sont envoyées. Les schémas des fichiers sont réunis par nom d'attribut : un attribut absent d'un fichier est vide, deux types incompatibles deviennent du texte. Pour que le schéma soit connu avant l'exécution (formulaires des nœuds suivants), indiquer un fichier d'exemple. « Attributs à recopier » ajoute à chaque entité lue des attributs de l'entité reçue (par exemple stem, le nom du fichier sans extension).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="readlistedfiles-path_attribute"></a>Chemin | Attribut qui contient le chemin du fichier à lire. | `path` |
| <a id="readlistedfiles-sample"></a>Fichier d'exemple | Facultatif : donne le schéma avant l'exécution, pour les formulaires des nœuds suivants. |  |
| <a id="readlistedfiles-copy_attributes"></a>Attributs à recopier | Attributs de l'entité reçue ajoutés à chaque entité lue (un nom déjà présent est refusé). |  |
| <a id="readlistedfiles-format"></a>Format | Déduit de l'extension si « auto ». | `auto` |
| <a id="readlistedfiles-layer"></a>Couche / feuille | Vide = la première. |  |
| <a id="readlistedfiles-cell_range"></a>Plage (Excel) | Ex. A1:F200. |  |
| <a id="readlistedfiles-encoding"></a>Encodage | Vide = détection automatique. |  |
| <a id="readlistedfiles-where"></a>Filtre attributaire | Condition SQL appliquée à la lecture (GDAL). |  |
| <a id="readlistedfiles-delimiter"></a>Séparateur (CSV) | Vide = détection automatique. |  |
| <a id="readlistedfiles-header"></a>Première ligne = en-têtes (CSV, Excel) | Décocher si pas de ligne d'en-têtes. | true |
| <a id="readlistedfiles-detect_types"></a>Détecter les types (CSV) | Sinon tout est lu en texte. | false |
| <a id="readlistedfiles-add_filename"></a>Ajouter le nom du fichier | Ajoute la colonne « filename ». | false |
| <a id="readlistedfiles-wkt_column"></a>Colonne WKT (CSV) | Colonne qui contient la géométrie en WKT. |  |
| <a id="readlistedfiles-x_column"></a>Colonne X (CSV) | À renseigner avec la colonne Y. |  |
| <a id="readlistedfiles-y_column"></a>Colonne Y (CSV) | À renseigner avec la colonne X. |  |
| <a id="readlistedfiles-crs"></a>Système de coordonnées (CSV) | Celui de la colonne WKT ou de X/Y. |  |
| <a id="readlistedfiles-on_error"></a>Fichier illisible | error : arrête le flux ; skip : ignore le fichier avec un avertissement. | `error` |

<a id="listfiles"></a>

### Lister des fichiers — `ListFiles`

Une entité par fichier d'un dossier (ou d'un fichier liste) : chemin, nom, taille, date.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sert à traiter des dossiers : relier à « Lire les fichiers listés » (ReadListedFiles) pour lire chacun, ou à « Lancer un flux » (RunFlow) pour exécuter un flux par fichier. Attributs : path (chemin complet), dir, name (avec extension), stem (sans extension), ext (avec le point, en minuscules), size (octets), mtime (dernière modification), is_dir. Les motifs sont ceux du shell (*.shp, commune_??.gpkg) et s'appliquent au nom ; plusieurs motifs s'additionnent. Un fichier liste (.txt) contient un chemin par ligne ; un CSV, une colonne « path » (ou, à défaut, la première colonne).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="listfiles-source"></a>Source | folder : un dossier ; list_file : un fichier qui liste les chemins. | `folder` |
| <a id="listfiles-folder"></a>Dossier | Dossier à parcourir (source folder). |  |
| <a id="listfiles-patterns"></a>Motifs | Motifs de nom de fichier, ex. *.shp ; vide = tous les fichiers. | `['*']` |
| <a id="listfiles-recursive"></a>Parcourir les sous-dossiers | Descend dans les sous-dossiers. | false |
| <a id="listfiles-include_dirs"></a>Lister aussi les dossiers | Ajoute une entité par dossier (is_dir = vrai). | false |
| <a id="listfiles-list_file"></a>Fichier liste | Fichier .txt (un chemin par ligne) ou .csv (colonne path). |  |
| <a id="listfiles-missing"></a>Fichier introuvable dans la liste | error : arrête le flux ; skip : l'ignore avec un avertissement. | `error` |
| <a id="listfiles-sort"></a>Trier par | Ordre des entités : chemin, date de modification ou taille. | `path` |

<a id="setvariables"></a>

### Poser des variables — `SetVariables`

Lit une valeur dans les données (un attribut, ou le nombre d'entités) et la pose en variable.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les entités passent sans changement. Les variables posées ici s'utilisent dans les nœuds situés **en aval** avec ${var:NOM}, dans n'importe quel paramètre : un nœud démarre quand tout son amont est terminé, donc la valeur est connue. L'analyse refuse une variable que rien ne pose en amont. Exemple : « Agréger » calcule la date la plus récente, SetVariables la pose en DATE_MAX, une lecture PostgreSQL filtre sur "date" > '${var:DATE_MAX}'.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="setvariables-variables"></a>Variables | Une ligne par variable posée. |  |

<a id="splittofiles"></a>

### Écrire un fichier par groupe — `SplitToFiles`

Écrit les entités dans plusieurs fichiers : le chemin se compose avec les valeurs d'attributs.

- **Entrées** : `input` — **Sorties** : `summary` — **Traitement** : au fil de l'eau

Écrire {attribut} dans le chemin crée un fichier par valeur de cet attribut, ex. D:\sorties\{departement}\communes.gpkg ou {departement}_{annee}.csv. Les caractères interdits dans un nom de fichier sont remplacés par _ et une valeur vide donne « _vide ». Les réglages du format sont ceux de l'écriture de ce format. Chaque fichier est ouvert une seule fois et le nombre de fichiers n'est pas limité : au-delà de 200 fichiers, les groupes suivants sont mis de côté dans le dossier de débordement puis écrits un à un. La sortie « summary » donne une entité par fichier écrit (chemin, nombre d'entités, format), pour la suite du flux (journal, SetVariables, RunFlow…).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="splittofiles-path"></a>Chemin du fichier | Chemin avec des {attributs} entre accolades ; dossiers créés si besoin. |  |
| <a id="splittofiles-format"></a>Format | Déduit de l'extension si « auto ». | `auto` |
| <a id="splittofiles-layer"></a>Couche / feuille | GeoPackage, MapInfo… : couche ; Excel : feuille. |  |
| <a id="splittofiles-mode"></a>Mode | create : échoue si un fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
| <a id="splittofiles-encoding"></a>Encodage | Vide = celui du format (utf-8). |  |
| <a id="splittofiles-delimiter"></a>Séparateur (CSV) | Séparateur de colonnes du CSV écrit. | `,` |
| <a id="splittofiles-compression"></a>Compression ((Geo)Parquet) | Compression des fichiers (Geo)Parquet. | `zstd` |
| <a id="splittofiles-partition_by"></a>Partitionner par ((Geo)Parquet) | Colonnes dont chaque valeur donne un sous-dossier de chaque fichier. |  |

## Rasters

<a id="terrainanalysis"></a>

### Analyse de terrain (pente, exposition, ombrage) — `TerrainAnalysis`

Calcule la pente, l'exposition, l'ombrage ou la rugosité d'un modèle numérique de terrain.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le raster d'entrée est un MNT (altitudes). Produits : slope (pente, en degrés ou en pourcentage), aspect (exposition en degrés depuis le nord, dans le sens horaire ; -1 sur un terrain plat), hillshade (ombrage de 0 à 255, selon « Azimut » et « Hauteur du soleil »), roughness (écart entre un pixel et ses voisins). Chaque produit devient une bande. Les dérivées utilisent la méthode de Horn (8 voisins). « Facteur Z » convertit les altitudes dans l'unité horizontale (1 si les deux sont en mètres). En degrés (WGS84), la taille des pixels est convertie en mètres à la latitude du pixel. Les pixels du bord et ceux dont un voisin est sans donnée restent sans donnée.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="terrainanalysis-products"></a>Produits | slope, aspect, hillshade ou roughness : une bande chacun. | `['slope']` |
| <a id="terrainanalysis-band"></a>Bande | Bande des altitudes (à partir de 1). | `1` |
| <a id="terrainanalysis-slope_unit"></a>Unité de pente | degrees ou percent. | `degrees` |
| <a id="terrainanalysis-z_factor"></a>Facteur Z | Multiplie les altitudes (1 si tout est en mètres). | `1.0` |
| <a id="terrainanalysis-azimuth"></a>Azimut (ombrage) | Direction du soleil, en degrés depuis le nord. | `315.0` |
| <a id="terrainanalysis-altitude"></a>Hauteur du soleil (ombrage) | Angle du soleil au-dessus de l'horizon. | `45.0` |

<a id="rastercalculator"></a>

### Calculatrice de rasters — `RasterCalculator`

Calcule de nouvelles bandes par expressions sur les bandes d'un ou plusieurs rasters (NDVI, masques, seuils).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque ligne crée une bande du raster résultat. Dans l'expression, les bandes s'appellent b1, b2… ou par leur nom (red, nir…) : (nir - red) / (nir + red). Opérateurs + - * / ** % ; comparaisons < <= == != >= > ; logiques & | ~ ; fonctions where(condition, si_vrai, si_faux), abs, sqrt, exp, log, log10, sin, cos, tan, arctan2, minimum, maximum… (calcul vectorisé et multithread, sans exécution de code). Un pixel sans donnée dans une bande utilisée reste sans donnée dans le résultat. Plusieurs entrées (liste « Entrées ») : celles qui suivent la première sont rééchantillonnées sur sa grille, et leurs bandes s'écrivent port_b1 ou port_nom (a_b1, b_red) ; avec une seule ligne dans une autre entrée, ce raster sert pour tous ceux de la première. Résultat en float32 par défaut.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="rastercalculator-inputs"></a>Entrées | Noms des ports d'entrée (lettres, chiffres et _). | `['input']` |
| <a id="rastercalculator-calculations"></a>Bandes calculées | Une ligne par bande du résultat. |  |
| <a id="rastercalculator-dtype"></a>Type du résultat | Type des pixels du résultat. | `float32` |
| <a id="rastercalculator-nodata"></a>Valeur sans donnée | Vide = NaN pour un résultat décimal, le maximum du type sinon. |  |
| <a id="rastercalculator-resampling"></a>Rééchantillonnage | Pour aligner les entrées suivantes sur la première. | `bilinear` |

<a id="contour"></a>

### Courbes de niveau — `Contour`

Trace les courbes de niveau d'un modèle numérique de terrain : une ligne par courbe, avec son altitude.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Une courbe est tracée tous les « Équidistance » (10 m : 100, 110, 120…), à partir de « Altitude de base », ou pour les altitudes de « Niveaux » si la liste est renseignée. Chaque ligne porte son altitude (« Attribut de l'altitude ») et les attributs du raster. « Longueur minimale » écarte les petites courbes (îlots). Les pixels sans donnée interrompent les courbes. Le raster est lu en mémoire (au plus 40 millions de pixels : découper d'abord au-delà). Les lignes sont dans le système du raster.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="contour-band"></a>Bande | Bande des altitudes (à partir de 1). | `1` |
| <a id="contour-interval"></a>Équidistance | Écart d'altitude entre deux courbes. | `10.0` |
| <a id="contour-base"></a>Altitude de base | Altitude à laquelle la série de courbes est ancrée. | `0.0` |
| <a id="contour-levels"></a>Niveaux | Altitudes précises (remplacent l'équidistance). |  |
| <a id="contour-min_length"></a>Longueur minimale | Écarte les courbes plus courtes (unité du système). | `0.0` |
| <a id="contour-elevation_attribute"></a>Attribut de l'altitude | Reçoit l'altitude de la courbe. | `elevation` |

<a id="cliprasterbymask"></a>

### Découpe de rasters — `ClipRasterByMask`

Découpe chaque raster par des polygones (communes, parcelles) ou par une emprise ; les pixels hors du polygone sont masqués.

- **Entrées** : `input`, `mask` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Par polygones : la deuxième entrée (« mask ») donne les polygones, dans n'importe quel système (ils sont reprojetés). « union » : un seul raster par raster d'origine, découpé par la réunion des polygones ; « each » : un raster par polygone, avec ses attributs (« Attributs du masque à copier ») : par exemple un MNT par commune. « Recadrer » réduit le raster à l'emprise des polygones ; sinon il garde sa taille et seuls les pixels hors polygone sont masqués. « Pixels touchés » inclut tout pixel que le polygone touche, même un peu. Par emprise : Ouest, Sud, Est, Nord dans le système du raster. Un polygone qui ne recouvre pas le raster ne donne rien.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="cliprasterbymask-by"></a>Découper par | features : polygones (entrée « mask ») ; extent : une emprise saisie. | `features` |
| <a id="cliprasterbymask-mode"></a>Polygones | union : un résultat par raster ; each : un résultat par polygone. | `union` |
| <a id="cliprasterbymask-extent"></a>Emprise | Ouest, Sud, Est, Nord dans le système du raster (par emprise). |  |
| <a id="cliprasterbymask-crop"></a>Recadrer | Réduit le raster à l'emprise de la découpe. | true |
| <a id="cliprasterbymask-all_touched"></a>Pixels touchés | Garde tout pixel que le polygone touche. | false |
| <a id="cliprasterbymask-mask_attributes"></a>Attributs du masque à copier | Mode « each » : ajoutés aux rasters produits. |  |

<a id="stackbands"></a>

### Empiler des bandes — `StackBands`

Réunit plusieurs rasters (une bande B04, une bande B08…) en un seul raster multibande.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les rasters reçus (par groupe, avec « Regrouper par ») deviennent les bandes d'un seul raster, sur la grille du premier. Si tous partagent la même grille, le résultat est un raster virtuel (instantané, sans copie) ; sinon (résolutions ou emprises différentes) ils sont rééchantillonnés sur la grille du premier et écrits dans un fichier. « Bande retenue » : la première bande de chaque raster, ou toutes. Les noms des bandes viennent de « Attribut du nom » (par exemple le nom du fichier) ou, à défaut, de la liste « Noms » puis des noms d'origine.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="stackbands-group_by"></a>Regrouper par | Un raster multibande par valeur ; vide = un seul pour tous. |  |
| <a id="stackbands-which"></a>Bande retenue | first : la première bande de chaque raster ; all : toutes ses bandes. | `first` |
| <a id="stackbands-name_attribute"></a>Attribut du nom | Attribut dont la valeur nomme la bande. |  |
| <a id="stackbands-names"></a>Noms | Noms des bandes dans l'ordre (sans attribut du nom). |  |
| <a id="stackbands-resampling"></a>Rééchantillonnage | Si les grilles diffèrent. | `bilinear` |

<a id="rasterize"></a>

### Entités vers raster — `Rasterize`

Transforme des polygones, lignes ou points en raster : valeur d'un attribut ou valeur fixe.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont dessinées dans un raster de pixels de « Taille du pixel » (dans l'unité de leur système de coordonnées), sur leur emprise ou sur celle de « Emprise ». Chaque pixel prend la valeur de « Attribut de la valeur » de l'entité qui le recouvre (la dernière en cas de recouvrement, ou la somme avec « add »), ou la « Valeur fixe » (1 par défaut : un masque). Les pixels non recouverts valent « Valeur de fond » (sans donnée si vide). « Pixels touchés » dessine tout pixel que l'entité touche, utile pour des lignes fines. Le dessin se fait par fenêtres de 512 pixels : la taille du raster n'est limitée que par le disque.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="rasterize-value_attribute"></a>Attribut de la valeur | Vide = valeur fixe. |  |
| <a id="rasterize-burn_value"></a>Valeur fixe | Valeur des pixels recouverts si aucun attribut n'est choisi. | `1.0` |
| <a id="rasterize-cell_size"></a>Taille du pixel | Dans l'unité du système de coordonnées des entités. | `10.0` |
| <a id="rasterize-extent"></a>Emprise | Ouest, Sud, Est, Nord ; vide = l'emprise des entités. |  |
| <a id="rasterize-crs"></a>Système de coordonnées | Vide = celui des entités. |  |
| <a id="rasterize-dtype"></a>Type | Type des pixels. | `float32` |
| <a id="rasterize-background"></a>Valeur de fond | Vide = sans donnée. |  |
| <a id="rasterize-all_touched"></a>Pixels touchés | Dessine tout pixel touché par l'entité. | false |
| <a id="rasterize-merge"></a>Recouvrements | replace : la dernière l'emporte ; add : somme. | `replace` |

<a id="rasterreader"></a>

### Lecture de rasters — `RasterReader`

Référence un ou plusieurs fichiers raster (GeoTIFF, COG, ASC, JP2, VRT…) : une entité par raster.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les pixels ne sont pas copiés : chaque entité décrit un raster (fichier, taille, bandes, système de coordonnées) et porte son emprise comme géométrie, ce qui permet de les voir sur la carte, de les filtrer ou de les joindre. Le chemin accepte un fichier, un motif (D:/mnt/*.tif, **/*.asc), une liste (« Autres fichiers ») ou une adresse web https:// (COG distant, lu par plages sans tout télécharger). « Bandes » choisit des bandes (numéros à partir de 1, ou noms) ; « Noms des bandes » nomme celles du résultat (utilisés dans RasterCalculator). Les formats lus sont ceux de GDAL.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="rasterreader-path"></a>Fichier | Fichier, motif (*.tif, **/*.asc) ou adresse https://. |  |
| <a id="rasterreader-paths"></a>Autres fichiers | Fichiers ou motifs lus en plus de « Fichier ». |  |
| <a id="rasterreader-bands"></a>Bandes | Numéros (à partir de 1) ou noms des bandes à garder ; vide = toutes. |  |
| <a id="rasterreader-band_names"></a>Noms des bandes | Noms donnés aux bandes du résultat, dans l'ordre. |  |
| <a id="rasterreader-add_filename"></a>Ajouter le nom du fichier | Ajoute l'attribut « filename ». | true |

<a id="mergerasters"></a>

### Mosaïque et composite de rasters — `MergeRasters`

Assemble des rasters côte à côte (dalles d'un MNT), ou en fait un composite temporel (médiane de plusieurs dates).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« spatial » : les rasters (même système de coordonnées, même taille de pixel, même nombre de bandes) sont posés sur leur emprise commune ; là où ils se recouvrent, le dernier qui a une donnée l'emporte (ordre de « Trier par »). Le résultat est un raster virtuel (VRT, instantané, sans copie) ou, avec « Écrire en GeoTIFF », un vrai fichier. « composite » : les rasters doivent avoir exactement la même grille ; chaque pixel prend la valeur first / last / median / mean / max / min de ses valeurs aux différentes dates (les pixels sans donnée sont ignorés) : un composite sans nuages, par exemple. « Regrouper par » fait un résultat par valeur d'attribut (une mosaïque par tuile, un composite par mois).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="mergerasters-mode"></a>Mode | spatial : mosaïque d'emprises ; composite : même emprise, dates différentes. | `spatial` |
| <a id="mergerasters-group_by"></a>Regrouper par | Un résultat par valeur ; vide = un seul résultat. |  |
| <a id="mergerasters-sort_by"></a>Trier par | Attribut qui ordonne les rasters (le dernier recouvre les autres). |  |
| <a id="mergerasters-method"></a>Méthode (composite) | Valeur retenue pour chaque pixel. | `median` |
| <a id="mergerasters-write"></a>Écrire en GeoTIFF | Spatial : écrit un vrai fichier au lieu d'un raster virtuel (toujours vrai pour un composite). | false |

<a id="polygonize"></a>

### Raster vers polygones — `Polygonize`

Transforme un raster en polygones : un polygone par ensemble de pixels voisins de même valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Pour un raster de classes (occupation du sol, résultat d'un seuil ou d'un reclassement), chaque zone d'un seul tenant de même valeur devient un polygone avec sa valeur (attribut « Attribut de la valeur ») et le numéro de la bande. « Connexité » : 4 (pixels qui se touchent par un côté) ou 8 (aussi par un coin). Les pixels sans donnée ne donnent rien. « Arrondir » regroupe les valeurs décimales proches (1 = au dixième) ; « Surface minimale » écarte les polygones plus petits (dans l'unité du système au carré). Le raster entier est lu en mémoire (au plus 60 millions de pixels : découper ou réduire la résolution au-delà). Les attributs du raster sont recopiés.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="polygonize-band"></a>Bande | Bande à vectoriser (à partir de 1). | `1` |
| <a id="polygonize-value_attribute"></a>Attribut de la valeur | Reçoit la valeur des pixels. | `value` |
| <a id="polygonize-connectivity"></a>Connexité | 4 ou 8 voisins. | `4` |
| <a id="polygonize-round_decimals"></a>Arrondir | Décimales conservées avant de regrouper ; vide = valeurs exactes. |  |
| <a id="polygonize-min_area"></a>Surface minimale | Ignore les polygones plus petits. | `0.0` |

<a id="stacsearch"></a>

### Recherche STAC — `STACSearch`

Cherche des images dans un catalogue STAC (Sentinel-2, Landsat, MNT…) et les référence sans les télécharger.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Interroge l'API STAC « Adresse du catalogue » (Earth Search d'Element 84 par défaut ; Planetary Computer : https://planetarycomputer.microsoft.com/api/stac/v1 avec la signature activée). Filtres : collections, emprise (longitude/latitude WGS84), dates (2024-06-01/2024-06-30), propriétés (JSON, ex. {"eo:cloud_cover": {"lt": 20}}). Avec « Géométries reçues », chaque entité de l'entrée donne une recherche (son emprise) et ses attributs sont recopiés. « Assets » liste les bandes voulues (ex. red, nir : leurs noms dans le catalogue) : chaque scène devient un raster virtuel dont les bandes sont ces assets, lus à la demande en HTTP (rien n'est téléchargé en entier). Chaque entité porte l'identifiant de la scène, sa date, sa collection et les propriétés demandées. Le proxy du poste est utilisé.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="stacsearch-api_url"></a>Adresse du catalogue | Racine de l'API STAC. | `https://earth-search.aws.element84.com/v1` |
| <a id="stacsearch-collections"></a>Collections | Collections à chercher. | `['sentinel-2-l2a']` |
| <a id="stacsearch-bbox"></a>Emprise (WGS84) | Ouest, sud, est, nord en degrés ; vide = pas de filtre (ou géométries reçues). |  |
| <a id="stacsearch-from_input"></a>Géométries reçues | Une recherche par entité de l'entrée, sur son emprise. | false |
| <a id="stacsearch-copy_attributes"></a>Attributs à recopier | Attributs de l'entité reçue ajoutés aux scènes trouvées. |  |
| <a id="stacsearch-datetime"></a>Dates | Une date ou un intervalle : 2024-06-01/2024-06-30 ; vide = toutes. |  |
| <a id="stacsearch-query"></a>Propriétés (JSON) | Filtre sur les propriétés, ex. {"eo:cloud_cover": {"lt": 20}}. |  |
| <a id="stacsearch-max_items"></a>Nombre maximal de scènes | Par recherche. | `50` |
| <a id="stacsearch-sortby"></a>Tri | Champ de tri, précédé de - pour décroissant. | `-properties.datetime` |
| <a id="stacsearch-assets"></a>Assets (bandes) | Noms des assets à garder, dans l'ordre : ils deviennent les bandes du raster. |  |
| <a id="stacsearch-properties"></a>Propriétés à recopier | Propriétés de la scène ajoutées comme attributs (eo:cloud_cover devient cloud_cover). | `['eo:cloud_cover']` |
| <a id="stacsearch-sign"></a>Signature | planetary_computer : signe les adresses (jeton de lecture temporaire). | `none` |

<a id="reclassifyraster"></a>

### Reclasser un raster — `ReclassifyRaster`

Remplace des plages de valeurs d'un raster par des classes (pente en 4 classes, NDVI en eau/sol/végétation).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque règle donne une plage [De ; À[ et la classe correspondante ; les règles sont examinées dans l'ordre, la première qui convient l'emporte. Pour une valeur exacte, mettre la même valeur en « De » et « À » et cocher « Inclure la borne haute ». « Autres pixels » : sans donnée, conservés tels quels (keep) ou remplacés par « Valeur par défaut ». Les pixels sans donnée restent sans donnée. Le résultat est une bande « class ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="reclassifyraster-band"></a>Bande | Bande à reclasser (à partir de 1). | `1` |
| <a id="reclassifyraster-rules"></a>Règles | Plages et classes, examinées dans l'ordre. |  |
| <a id="reclassifyraster-others"></a>Autres pixels | nodata : sans donnée ; keep : valeur d'origine ; default : « Valeur par défaut ». | `nodata` |
| <a id="reclassifyraster-default_value"></a>Valeur par défaut | Pour « Autres pixels » = default. | `0.0` |
| <a id="reclassifyraster-dtype"></a>Type du résultat | Type des pixels du résultat. | `uint8` |
| <a id="reclassifyraster-nodata"></a>Valeur sans donnée | Vide = 255 pour uint8, convention du type sinon. |  |

<a id="warpraster"></a>

### Reprojection de rasters — `WarpRaster`

Change le système de coordonnées d'un raster, avec choix de la résolution et du rééchantillonnage.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le raster est recalculé dans le « Système cible » (EPSG:2154, EPSG:4326…). « Résolution » est la taille d'un pixel dans l'unité du système cible ; vide ou 0 = choisie pour garder à peu près le même nombre de pixels. Rééchantillonnage : nearest pour des classes (occupation du sol), bilinear ou cubic pour des valeurs continues (altitude, réflectance), average pour réduire la résolution. Le calcul se fait par fenêtres de 512 pixels, en parallèle : la mémoire reste bornée quelle que soit la taille du raster. Les pixels hors de l'image d'origine deviennent « sans donnée ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="warpraster-crs"></a>Système cible | Ex. EPSG:2154. |  |
| <a id="warpraster-resolution"></a>Résolution | Taille du pixel, dans l'unité du système cible ; 0 = automatique. | `0.0` |
| <a id="warpraster-resampling"></a>Rééchantillonnage | Méthode de calcul des nouveaux pixels. | `bilinear` |
| <a id="warpraster-nodata"></a>Valeur sans donnée | Vide = celle du raster, sinon la convention du type. |  |

<a id="resampleraster"></a>

### Rééchantillonner / aligner — `ResampleRaster`

Change la taille des pixels d'un raster, ou l'aligne sur la grille d'un autre raster (même pixels, même emprise).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sans « Aligner sur un raster de référence » : le raster garde son système et son emprise, avec des pixels de « Taille du pixel » (ou multipliés par « Facteur » : 2 = pixels deux fois plus grands). Avec la référence (deuxième entrée) : le raster est recalculé sur exactement la grille de la référence (système, taille et emprise), ce qui permet de combiner des images de résolutions différentes. nearest convient aux classes, bilinear / cubic aux valeurs continues, average pour réduire la résolution.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="resampleraster-align"></a>Aligner sur un raster de référence | Ajoute une entrée « reference » dont la grille est reprise. | false |
| <a id="resampleraster-cell_size"></a>Taille du pixel | Dans l'unité du système ; 0 = ne pas fixer. | `0.0` |
| <a id="resampleraster-factor"></a>Facteur | Multiplie la taille du pixel (2 = deux fois plus grand). | `1.0` |
| <a id="resampleraster-resampling"></a>Rééchantillonnage | Méthode de calcul des nouveaux pixels. | `bilinear` |
| <a id="resampleraster-nodata"></a>Valeur sans donnée | Vide = celle du raster. |  |

<a id="rasterstatistics"></a>

### Statistiques de rasters — `RasterStatistics`

Calcule le minimum, le maximum, la moyenne, l'écart-type et des percentiles de chaque raster, en attributs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque raster reçoit, pour chaque bande choisie, les statistiques demandées dans des attributs `bande_statistique` (ndvi_mean…). min, max, mean, std, count (pixels avec donnée) et nodata_count sont exacts et calculés par fenêtres : la mémoire reste bornée. Les percentiles (p05 à p95) sont estimés sur une lecture réduite à 4 millions de pixels environ. Utile pour filtrer des scènes (couverture trop faible), choisir une étendue de couleurs ou contrôler un résultat.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="rasterstatistics-bands"></a>Bandes | Numéros (de 1) ou noms ; vide = toutes les bandes. |  |
| <a id="rasterstatistics-stats"></a>Statistiques | min, max, mean, std, count, nodata_count, p05, p25, p50, p75, p95. | `['min', 'max', 'mean']` |

<a id="zonalstatistics"></a>

### Statistiques zonales — `ZonalStatistics`

Calcule des statistiques d'un raster sous chaque polygone : moyenne, somme, minimum, maximum, majoritaire…

- **Entrées** : `zones`, `raster` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque polygone de « zones » reçoit les statistiques des pixels du raster qu'il recouvre (un pixel compte s'il a son centre dans le polygone, ou s'il est touché avec « Pixels touchés »). Les colonnes s'appellent `bande_statistique` (par exemple ndvi_mean). Statistiques : count, sum, mean, min, max, std, median, majority (valeur la plus fréquente : classes d'occupation du sol), unique (nombre de valeurs distinctes), area (surface des pixels valides, dans l'unité du système au carré). Les pixels sans donnée sont ignorés. S'il y a plusieurs rasters (dates, scènes), le résultat a une ligne par zone et par raster, avec les « Attributs du raster à ajouter » (par exemple la date). Un polygone hors du raster a un compte à 0 et des statistiques vides. Les polygones sont reprojetés dans le système du raster.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="zonalstatistics-bands"></a>Bandes | Numéros (de 1) ou noms des bandes ; vide = la première. |  |
| <a id="zonalstatistics-stats"></a>Statistiques | count, sum, mean, min, max, std, median, majority, unique ou area. | `['mean']` |
| <a id="zonalstatistics-all_touched"></a>Pixels touchés | Compte tout pixel que le polygone touche. | false |
| <a id="zonalstatistics-raster_attributes"></a>Attributs du raster à ajouter | Recopiés sur chaque ligne (la date d'une scène…). |  |
| <a id="zonalstatistics-prefix"></a>Préfixe | Ajouté devant le nom de chaque colonne de statistique. |  |

<a id="samplerastervalues"></a>

### Valeur du raster sous des points — `SampleRasterValues`

Ajoute à chaque point la valeur d'un raster à cet endroit (altitude d'un point, occupation du sol d'une parcelle).

- **Entrées** : `points`, `raster` — **Sorties** : `output`, `Outside` — **Traitement** : au fil de l'eau

Chaque entité de « points » reçoit, pour chaque bande choisie, la valeur du pixel qui la contient (attribut `préfixe` + nom de la bande). Les points sont reprojetés dans le système du raster. Avec plusieurs rasters, le premier qui recouvre le point est utilisé, et `_raster` donne son numéro (de 0). Les points hors de tous les rasters, ou sur un pixel sans donnée, ont une valeur vide ; les premiers sortent sur « Outside ». Les entités qui ne sont pas des points sont lues par leur point représentatif (centre de gravité intérieur).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="samplerastervalues-bands"></a>Bandes | Numéros (de 1) ou noms des bandes ; vide = toutes. |  |
| <a id="samplerastervalues-prefix"></a>Préfixe | Ajouté devant le nom de chaque bande dans les attributs. |  |

<a id="rasterwriter"></a>

### Écriture de rasters — `RasterWriter`

Écrit chaque raster dans un fichier GeoTIFF ou COG (cloud optimized), avec vues d'ensemble.

- **Entrées** : `input` — **Sorties** : `summary` — **Traitement** : au fil de l'eau

Le chemin est un fichier, ou un modèle avec des {attributs} (un fichier par raster : D:/sorties/{filename}), ou un dossier (le fichier prend le nom du raster d'origine). « cog » écrit un GeoTIFF optimisé pour le nuage (tuiles, vues d'ensemble, lisible par plages sur le web) ; « gtiff » un GeoTIFF tuilé ordinaire, avec vues d'ensemble si demandé. Compression sans perte (deflate ou zstd). Sur le partage de l'équipe, le fichier est écrit en local puis publié d'un bloc. Un raster déjà existant est refusé en mode « create ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="rasterwriter-path"></a>Fichier ou dossier | Fichier .tif, modèle avec {attributs}, ou dossier. |  |
| <a id="rasterwriter-format"></a>Format | cog : cloud optimized GeoTIFF ; gtiff : GeoTIFF tuilé. | `cog` |
| <a id="rasterwriter-compression"></a>Compression | Compression sans perte. | `deflate` |
| <a id="rasterwriter-overviews"></a>Vues d'ensemble | Ajoute les vues d'ensemble (pyramide) : affichage rapide. | true |
| <a id="rasterwriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace. | `overwrite` |

## Écriture

<a id="csvwriter"></a>

### Écriture CSV — `CsvWriter`

Écrit les entités dans un fichier CSV (la géométrie en WKT).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Encodage utf-8. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="csvwriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="csvwriter-delimiter"></a>Séparateur | Séparateur de colonnes. | `,` |
| <a id="csvwriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

<a id="excelwriter"></a>

### Écriture Excel — `ExcelWriter`

Écrit les entités dans une feuille d'un classeur Excel (.xlsx).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures Excel peuvent viser des feuilles différentes du même classeur : en mode « overwrite », seule la feuille visée est remplacée. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="excelwriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="excelwriter-sheet"></a>Feuille | Nom de la feuille ; vide = « Feuil1 ». |  |
| <a id="excelwriter-mode"></a>Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

<a id="flatgeobufwriter"></a>

### Écriture FlatGeobuf — `FlatGeobufWriter`

Écrit les entités dans un fichier FlatGeobuf (.fgb), avec index spatial.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="flatgeobufwriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="flatgeobufwriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

<a id="geojsonwriter"></a>

### Écriture GeoJSON — `GeoJsonWriter`

Écrit les entités dans un fichier GeoJSON.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Pour suivre la norme, reprojeter avant en WGS 84 (EPSG:4326). Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geojsonwriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="geojsonwriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

<a id="geopackagewriter"></a>

### Écriture GeoPackage — `GeoPackageWriter`

Écrit les entités dans une couche d'un GeoPackage (.gpkg).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures GeoPackage peuvent viser des couches différentes du même fichier : en mode « overwrite », seule la couche visée est remplacée. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geopackagewriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="geopackagewriter-layer"></a>Couche | Nom de la couche ; vide = le nom du fichier. |  |
| <a id="geopackagewriter-mode"></a>Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

<a id="geoparquetwriter"></a>

### Écriture (Geo)Parquet — `GeoParquetWriter`

Écrit les entités dans un fichier Parquet (GeoParquet s'il y a une géométrie) ou un dossier partitionné.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

« Partitionner par » écrit un sous-dossier par valeur : « Fichier » est alors un dossier. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="geoparquetwriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="geoparquetwriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
| <a id="geoparquetwriter-compression"></a>Compression | zstd : compact et rapide ; none : aucune. | `zstd` |
| <a id="geoparquetwriter-partition_by"></a>Partitionner par | Colonnes dont chaque valeur donne un sous-dossier ; vide = un seul fichier. |  |

<a id="mapinfowriter"></a>

### Écriture MapInfo — `MapInfoWriter`

Écrit les entités dans une table MapInfo (.tab) ou un fichier MIF/MID (.mif).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Le format (TAB ou MIF) est déduit de l'extension. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="mapinfowriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="mapinfowriter-encoding"></a>Encodage | Vide = celui du format. |  |
| <a id="mapinfowriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

<a id="oraclewriter"></a>

### Écriture Oracle — `OracleWriter`

Écrit les entités dans une table Oracle (créée si besoin), en une seule transaction.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Modes : create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. Les lignes sont écrites dans une transaction ; mais Oracle valide aussitôt la création et la suppression d'une table (create, overwrite). La géométrie est écrite en SDO_GEOMETRY avec le SRID des données ; à la création, les métadonnées (USER_SDO_GEOM_METADATA) et l'index spatial sont créés. Les noms de table et de colonnes créés sont en majuscules.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="oraclewriter-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="oraclewriter-table"></a>Table | Schéma puis table (existante, ou nouvelle à créer : taper son nom). |  |
| <a id="oraclewriter-mode"></a>Mode | create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. | `append` |
| <a id="oraclewriter-key"></a>Clé (upsert / clé primaire) | Colonnes identifiant une ligne. |  |
| <a id="oraclewriter-geometry_column"></a>Colonne géométrique créée | Nom de la colonne géométrique, utilisé quand la table est créée. | `geom` |
| <a id="oraclewriter-create_spatial_index"></a>Index spatial | Crée un index spatial avec la table. | true |
| <a id="oraclewriter-crs"></a>Système de coordonnées | Vide = celui des données. |  |

<a id="postgreswriter"></a>

### Écriture PostgreSQL / PostGIS — `PostgresWriter`

Écrit les entités dans une table PostgreSQL (créée si besoin), en une seule transaction.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Modes : « create » échoue si la table existe ; « overwrite » la recrée ; « append » ajoute ; « truncate » la vide puis insère ; « upsert » met à jour les lignes de même clé et insère les autres. Tout est écrit dans une transaction : en cas d'erreur, la table reste comme avant. Les colonnes des données doivent exister dans la table (hors création). Un index spatial GIST est créé avec la table. Avec « auto », polygones et lignes sont créés en « Multi » (les géométries simples sont promues) ; sinon le type du premier lot : le fixer explicitement si les données sont hétérogènes.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="postgreswriter-connection"></a>Connexion | Connexion nommée (menu Connexions). |  |
| <a id="postgreswriter-table"></a>Table | Schéma puis table (existante, ou nouvelle à créer : taper son nom). |  |
| <a id="postgreswriter-mode"></a>Mode | create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. | `append` |
| <a id="postgreswriter-key"></a>Clé (upsert / clé primaire) | Colonnes identifiant une ligne. |  |
| <a id="postgreswriter-geometry_column"></a>Colonne géométrique créée | Nom de la colonne géométrique, utilisé quand la table est créée. | `geom` |
| <a id="postgreswriter-geometry_type"></a>Type de géométrie (création) | Type de la colonne créée. auto : multi-surfaces et multi-lignes pour les polygones et les lignes, sinon le type du premier lot. | `auto` |
| <a id="postgreswriter-create_spatial_index"></a>Index spatial (GIST) | Crée un index spatial GIST avec la table. | true |
| <a id="postgreswriter-crs"></a>Système de coordonnées | Vide = celui des données. |  |

<a id="spatialitewriter"></a>

### Écriture SQLite / SpatiaLite — `SpatiaLiteWriter`

Écrit les entités dans une table d'une base SpatiaLite (.sqlite), créée si besoin.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures peuvent viser des tables différentes de la même base : en mode « overwrite », seule la table visée est remplacée. Une base créée par Carriole est une base SpatiaLite (lue par QGIS…). Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="spatialitewriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="spatialitewriter-layer"></a>Table | Nom de la table ; vide = le nom du fichier. |  |
| <a id="spatialitewriter-mode"></a>Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

<a id="shapefilewriter"></a>

### Écriture Shapefile — `ShapefileWriter`

Écrit les entités dans un Shapefile (.shp).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Noms de colonnes limités à 10 caractères (les renommages sont journalisés), un seul type de géométrie, limite de 2 Go. Un fichier .cpg indique l'encodage. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| <a id="shapefilewriter-path"></a>Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| <a id="shapefilewriter-encoding"></a>Encodage | Vide = utf-8 (écrit dans le .cpg). |  |
| <a id="shapefilewriter-mode"></a>Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
