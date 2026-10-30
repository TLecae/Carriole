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

### Construire une liste — `BuildList` {#buildlist}

Regroupe les valeurs d'un attribut dans une liste : une entité par groupe, avec la liste des valeurs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont regroupées par « Regrouper par » (vide = un seul groupe) ; la liste réunit les valeurs de « Attribut des valeurs », dans l'ordre d'arrivée (ou triées, ou sans doublon). Les valeurs vides sont ignorées. Géométrie : « none » (aucune) ou « first » (celle de la première entité du groupe). La liste est un attribut de type liste : « Éclater une liste » fait l'inverse.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Vide = un seul groupe pour toutes les entités. |  |
| Attribut des valeurs | Les valeurs qui vont dans la liste. |  |
| Attribut liste | Nom de la liste créée (vide = `attribut` + _liste). |  |
| Sans doublon | Une seule fois chaque valeur. | false |
| Trier | Valeurs triées ; sinon dans l'ordre d'arrivée. | false |
| Géométrie | none : aucune ; first : celle de la première entité du groupe. | `none` |

### Convertir le type d'attributs — `CastAttributes` {#castattributes}

Change le type de plusieurs attributs à la fois : texte, entier, décimal, booléen, date.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne convertit un attribut en place. Une valeur impossible à convertir (« abc » en entier) envoie l'entité sur « Rejected » avec le motif, sauf si « Valeur non convertible » vaut null : elle devient alors vide. « Virgule décimale » lit les nombres à la française (« 1 234,5 »). Booléens reconnus : true/false, oui/non, 1/0. Une valeur vide reste vide.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Conversions | Une ligne par attribut converti. |  |
| Valeur non convertible | reject : l'entité part sur « Rejected » ; null : la valeur devient vide. | `reject` |

### Convertir une date — `ConvertDate` {#convertdate}

Lit une date écrite en texte (ou un horodatage) et la convertit : date, date-heure, texte formaté, secondes Unix.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« Format de lecture » est un format strptime (%d/%m/%Y, %Y%m%d, %Y-%m-%d %H:%M:%S…) ; vide = lecture automatique des formats ISO (2026-10-11, 2026-10-11T08:30:00). Un attribut déjà de type date ou date-heure n'a pas besoin de format. Résultat : date, date-heure, texte (avec « Format d'écriture », ex. %d/%m/%Y) ou epoch (secondes depuis 1970, en UTC). Une valeur illisible envoie l'entité sur « Rejected », ou devient vide si « Valeur illisible » vaut null. Codes : %Y année, %m mois, %d jour, %H heure, %M minute, %S seconde, %B nom du mois, %A nom du jour.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut | Attribut qui contient la date. |  |
| Format de lecture | Format strptime du texte, ex. %d/%m/%Y ; vide = ISO automatique. |  |
| Résultat | date, datetime, text ou epoch (secondes Unix, UTC). | `date` |
| Format d'écriture | Pour « text » : format strftime, ex. %d/%m/%Y. | `%Y-%m-%d` |
| Attribut résultat | Vide = remplace l'attribut d'origine. |  |
| Valeur illisible | reject : l'entité part sur « Rejected » ; null : la valeur devient vide. | `reject` |

### Créer des attributs — `AddAttributes` {#addattributes}

Ajoute ou remplace des attributs calculés par des expressions SQL.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque attribut est calculé par une expression SQL DuckDB, dans l'ordre : une expression peut utiliser les attributs créés plus haut. Exemples : "surface" * 2 ; upper("nom") ; CASE WHEN "pop" > 1000 THEN 'ville' ELSE 'village' END ; ST_Area(geometry). Une entité dont l'expression échoue (ou dont la valeur ne se convertit pas dans le type demandé) part sur « Rejected » avec le motif ; avec « En cas d'échec : null », l'attribut reste vide.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs à créer | Calculés dans l'ordre : chacun peut utiliser les précédents. |  |
| En cas d'échec | reject : l'entité part sur « Rejected » avec le motif ; null : l'attribut reste vide. | `reject` |

### Découper un texte — `SplitText` {#splittext}

Découpe un attribut texte selon un séparateur, en plusieurs attributs ou en une liste.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« columns » crée `préfixe_1`, `préfixe_2`… (nombre fixé, pour que le schéma soit connu avant l'exécution) : s'il y a plus de morceaux que de colonnes, le reste est laissé dans la dernière ; s'il y en a moins, les colonnes en trop sont vides. « list » produit une liste (à éclater avec ExplodeList). Un texte vide donne des morceaux vides.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut à découper | Attribut texte à découper. |  |
| Séparateur | Texte exact (pas une expression régulière). | `,` |
| Résultat | columns : une colonne par morceau (préfixe_1, préfixe_2…) ; list : une seule colonne de type liste, à éclater ensuite avec « Éclater une liste ». | `columns` |
| Préfixe des attributs créés | Vide = nom de l'attribut d'origine. |  |
| Nombre de colonnes (mode columns) | Nombre de colonnes créées : le surplus reste dans la dernière, les colonnes en trop sont vides. | `2` |
| Retirer les espaces autour de chaque morceau | Retire les espaces au début et à la fin de chaque morceau. | true |

### Dépivoter (colonnes en lignes) — `PivotLonger` {#pivotlonger}

Transforme des colonnes en lignes : une entité par colonne choisie, avec son nom et sa valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque entité reçue donne une entité par attribut de « Attributs à dépivoter » : les autres attributs (et la géométrie) sont recopiés, « Attribut du nom » reçoit le nom de la colonne d'origine et « Attribut de la valeur » sa valeur. Si les colonnes n'ont pas le même type, les valeurs deviennent du texte. Les valeurs vides sont ignorées sauf si « Garder les valeurs vides » est coché. Exemple : (commune, pop_2020, pop_2021) devient (commune, annee, population).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs à dépivoter | Les colonnes qui deviennent des lignes. |  |
| Attribut du nom | Reçoit le nom de la colonne d'origine. | `attribute` |
| Attribut de la valeur | Reçoit la valeur de la colonne. | `value` |
| Garder les valeurs vides | Décoché : une valeur vide ne donne pas d'entité. | false |

### Extraire du JSON — `ExtractJson` {#extractjson}

Lit un attribut qui contient du JSON : extrait des valeurs dans de nouveaux attributs, ou éclate un tableau.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne de « Valeurs à extraire » lit un chemin (a.b, a[0].b, $.a.b) et le range dans un attribut typé. Avec « Tableau à éclater » (par exemple items ou $.results), le JSON de chaque entité donne **une entité par élément du tableau** : les chemins sont alors lus dans l'élément (chemin vide = l'élément entier, en texte), et les autres attributs sont recopiés. Un chemin absent ou une valeur non convertible donne une valeur vide ; un JSON invalide aussi, ou l'entité part sur « Rejected » si « JSON invalide » vaut reject. Typique après un appel HTTP (HttpRequest).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut JSON | Attribut texte qui contient le JSON. |  |
| Valeurs à extraire | Un chemin et l'attribut qui le reçoit. |  |
| Tableau à éclater | Chemin d'un tableau : une entité par élément ; vide = une entité par ligne. |  |
| Retirer l'attribut JSON | Retire l'attribut d'origine du résultat. | false |
| JSON invalide | null : les valeurs extraites sont vides ; reject : l'entité part sur « Rejected ». | `null` |

### Extraire du texte — `ExtractText` {#extracttext}

Extrait une partie d'un attribut texte : sous-chaîne, avant/après un séparateur, ou captures d'une expression régulière.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« substring » : à partir de la position « Début » (1 = premier caractère), sur « Longueur » caractères (vide = jusqu'à la fin) ; un début négatif compte depuis la fin. « before » / « after » : ce qui précède / suit la première occurrence du séparateur (vide si absent). « regex » : une expression régulière avec des groupes **nommés** `(?P<code>\d{5})` ; chaque groupe donne un attribut (préfixé par « Préfixe »), vide si le texte ne correspond pas. Syntaxe RE2. Exemple : `(?P<dep>\d{2})(?P<com>\d{3})` sur un code INSEE.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut | Attribut texte à analyser. |  |
| Extraction | substring, before, after ou regex. | `substring` |
| Début (substring) | Position du premier caractère (1 = premier ; négatif depuis la fin). | `1` |
| Longueur (substring) | Nombre de caractères ; vide = jusqu'à la fin. |  |
| Séparateur (before / after) | Texte exact (pas une expression régulière). |  |
| Expression (regex) | Expression régulière RE2 avec des groupes nommés (?P<nom>…). |  |
| Attribut créé | substring / before / after : nom du résultat (vide = `attribut` + _extrait). |  |
| Préfixe (regex) | Ajouté devant le nom de chaque groupe. |  |

### Garder des attributs — `RetainAttributes` {#retainattributes}

Ne conserve que les attributs choisis, dans l'ordre indiqué.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La géométrie est conservée sauf si on la retire explicitement. Un attribut absent est une erreur.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs à garder | Dans cet ordre. |  |
| Garder la géométrie | Décocher pour retirer aussi la géométrie. | true |

### Gérer des attributs — `RefactorAttributes` {#refactorattributes}

Crée (expression), renomme ou supprime des attributs, règle après règle.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Les règles s'appliquent dans l'ordre. « set » : calcule l'attribut par une expression SQL (ex. "surface" / 10000) ; « rename » : attribut → nouveau nom ; « remove » : supprime l'attribut. Une entité dont l'expression échoue part sur « Rejected » avec le motif.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Règles | Appliquées dans l'ordre. |  |

### Pivoter (lignes en colonnes) — `PivotWider` {#pivotwider}

Transforme les valeurs d'un attribut en colonnes : une ligne par groupe, une colonne par valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont regroupées par « Regrouper par » ; chaque valeur de « Attribut à pivoter » devient une colonne (préfixe + valeur), remplie avec « Attribut des valeurs » combiné par la fonction choisie : first (la première), sum, mean, min, max, count (nombre de lignes) ou concat (valeurs réunies par une virgule). Pour que le schéma soit connu avant l'exécution (formulaires des nœuds suivants), lister les valeurs attendues dans « Colonnes » ; sinon elles sont découvertes dans les données, par ordre alphabétique. La géométrie n'est pas conservée. Exemple : (commune, annee, population) devient (commune, pop_2020, pop_2021, …).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Attributs qui identifient une ligne du résultat. |  |
| Attribut à pivoter | Ses valeurs deviennent les noms des colonnes. |  |
| Attribut des valeurs | Valeur placée dans chaque colonne (inutile avec count). |  |
| Fonction | Comment combiner plusieurs lignes d'un même groupe et d'une même valeur. | `first` |
| Préfixe des colonnes | Ajouté devant chaque valeur pivotée. |  |
| Colonnes | Valeurs attendues (donnent le schéma avant l'exécution) ; vide = découvertes dans les données. |  |

### Remplacer du texte — `ReplaceText` {#replacetext}

Remplace du texte dans des attributs : texte exact ou expression régulière, en place ou dans un nouvel attribut.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque attribut choisi est converti en texte puis traité. Texte exact : toutes les occurrences de « Chercher » sont remplacées. Expression régulière (syntaxe RE2) : « Remplacer par » peut citer les groupes (\1, \2). « Valeur entière » remplace seulement les valeurs égales à « Chercher » (par exemple « N/A » par une valeur vide). Avec un suffixe, le résultat va dans un nouvel attribut `nom` + suffixe et l'original est conservé. Les valeurs vides restent vides.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs | Attributs à traiter. |  |
| Chercher | Texte exact, ou expression régulière si « Expression régulière » est coché. |  |
| Remplacer par | Vide = supprimer le texte trouvé. |  |
| Expression régulière | « Chercher » est une expression régulière (RE2). | false |
| Valeur entière | Remplace seulement les valeurs entières égales à « Chercher ». | false |
| Ignorer la casse | Ne distingue pas majuscules et minuscules. | false |
| Suffixe du nouvel attribut | Vide = remplace l'attribut ; sinon crée `nom` + suffixe. |  |

### Renommer des attributs — `RenameAttributes` {#renameattributes}

Renomme des attributs : ancien nom, nouveau nom.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Les renommages s'appliquent ensemble (on peut échanger deux noms). Le nouveau nom ne doit pas déjà exister.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Renommages |  |  |

### Renommer en masse — `RenameAttributesByPattern` {#renameattributesbypattern}

Renomme tous les attributs (ou une sélection) : préfixe, suffixe, remplacement, casse, accents.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les opérations s'appliquent dans cet ordre au nom de chaque attribut : remplacement (texte ou expression régulière), retrait des accents, espaces et caractères spéciaux remplacés par _, casse, puis préfixe et suffixe. Sans sélection, tous les attributs sont renommés (la géométrie jamais). Deux attributs qui auraient le même nouveau nom, ou un nom déjà pris par un attribut non renommé, sont refusés. Utile avant une écriture Shapefile (noms de 10 caractères) ou pour normaliser un export.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs | Vide = tous les attributs (hors géométrie). |  |
| Remplacer | Texte (ou expression régulière) à remplacer dans les noms. |  |
| Par | Texte de remplacement (\1 pour un groupe de l'expression). |  |
| Expression régulière | « Remplacer » est une expression régulière. | false |
| Retirer les accents | é devient e, ç devient c… | false |
| Nettoyer | Remplace espaces et caractères spéciaux par _ (« Code INSEE » devient Code_INSEE). | false |
| Casse | none : inchangée ; lower / upper ; snake : minuscules_avec_tirets_bas (CodeInsee devient code_insee). | `none` |
| Préfixe | Ajouté devant chaque nom. |  |
| Suffixe | Ajouté derrière chaque nom. |  |
| Longueur maximale | Coupe les noms trop longs (0 = pas de limite ; 10 pour un Shapefile). | `0` |

### Retirer des attributs — `DropAttributes` {#dropattributes}

Supprime les attributs choisis ; tous les autres sont conservés.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Un attribut absent est une erreur de configuration (sauf si « Ignorer les absents » est coché).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs à retirer | Attributs à supprimer ; vide = rien n'est retiré. |  |
| Retirer aussi la géométrie | Retire aussi la géométrie des entités. | false |
| Ignorer les attributs absents | Décoché : un attribut absent des données est une erreur ; coché : il est ignoré. | false |

### Table de correspondance — `MapValues` {#mapvalues}

Remplace les valeurs d'un attribut selon une table « valeur d'origine → valeur ».

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La comparaison se fait sur le texte de la valeur (1 et « 1 » sont égaux). Pour les valeurs absentes de la table : « keep » les conserve, « default » met la valeur par défaut, « null » les vide, « reject » envoie l'entité sur « Rejected ». Le résultat est du texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut à traduire | Attribut dont les valeurs sont traduites. |  |
| Attribut résultat | Vide = remplace l'attribut d'origine. |  |
| Correspondances | Une ligne par valeur à traduire. Le résultat est du texte. |  |
| Valeur absente de la table | Pour une valeur absente de la table — keep : on la garde ; default : on met la valeur par défaut ; null : on la vide ; reject : l'entité part sur « Rejected ». | `keep` |
| Valeur par défaut | Utilisée quand « Valeur absente de la table » vaut default. |  |
| Ignorer la casse | Ne distingue pas majuscules et minuscules. | false |

### Valeurs vides — `ReplaceNulls` {#replacenulls}

Remplace les valeurs vides (NULL) par une valeur, ou transforme une valeur donnée en vide.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« fill » : les vides deviennent la valeur choisie (convertie au type de l'attribut ; impossible = erreur de configuration). « to_null » : les valeurs égales à la valeur choisie (ex. « -1 », « N/A ») deviennent vides. « Compter le texte vide » traite aussi « » et les espaces comme des vides. Sans attribut : tous.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs | Vide = tous (hors géométrie). |  |
| Action | fill : remplace les valeurs vides par « Valeur » ; to_null : vide les valeurs égales à « Valeur » (ex. -1, N/A). | `fill` |
| Valeur | fill : valeur écrite à la place des vides ; to_null : valeur à considérer comme vide. |  |
| Compter le texte vide comme vide | Traite aussi le texte vide et les espaces comme des valeurs vides. | false |

### Éclater une liste — `ExplodeList` {#explodelist}

Produit une entité par élément d'un attribut de type liste (les autres attributs sont recopiés).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'attribut doit être une liste (ex. issue d'un tableau PostgreSQL ou de « Découper un texte » en mode liste). Une liste vide ou nulle ne produit aucune entité, sauf si « Garder les listes vides » est coché (une entité avec élément vide). « Attribut indice » numérote les éléments à partir de 0.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut liste | Attribut de type liste à éclater (une entité par élément). |  |
| Attribut élément | Vide = remplace l'attribut liste. |  |
| Attribut indice | Facultatif. |  |
| Garder les listes vides | Une liste vide ou nulle donne quand même une entité (élément vide) au lieu de disparaître. | false |

## Calculs

### Agréger — `Aggregate` {#aggregate}

Regroupe les entités par clé : une entité par groupe, avec le nombre d'entités et des sommes.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les attributs de l'entité agrégée sont ceux de la première entité du groupe. Géométrie : « collect » réunit les géométries sans les fusionner (MultiPolygon, MultiPoint… ou collection hétérogène), « first » garde celle de la première entité, « none » la retire. Pour fusionner réellement les polygones, utiliser le Dissolve.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Vide = tout dans un seul groupe. |  |
| Attribut du nombre d'entités | Vide = pas de comptage. | `_count` |
| Attributs à additionner | Créent `attribut_sum`. |  |
| Géométrie | collect : réunit les géométries du groupe sans les fusionner ; first : celle de la première entité ; none : aucune géométrie. | `collect` |

### Calculer des statistiques — `StatisticsByGroup` {#statisticsbygroup}

Calcule des statistiques par groupe : somme, moyenne, min, max, médiane, comptage…

- **Entrées** : `input` — **Sorties** : `output`, `summary` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sortent sur « output » (avec les statistiques de leur groupe si « Ajouter aux entités » est coché) et une ligne par groupe sort sur « summary ». Les colonnes s'appellent `attribut_fonction` (ex. pop_sum) ; `_count` est le nombre d'entités du groupe. Somme, moyenne, médiane et écart-type convertissent la valeur en nombre (non convertible = ignorée). Sans groupe, tout est un seul groupe.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Vide = un seul groupe pour toutes les entités. |  |
| Attributs à analyser | Attributs sur lesquels calculer les statistiques (les colonnes s'appellent attribut_fonction, ex. pop_sum). |  |
| Statistiques | Calculées pour chaque attribut. | `['count', 'sum', 'mean', 'min', 'max']` |
| Compter les entités (_count) | Ajoute _count : le nombre d'entités du groupe. | true |
| Ajouter aux entités | Recopie les statistiques sur chaque entité. | true |

### Code Python — `PythonScript` {#pythonscript}

Exécute une fonction Python `process(table, ctx)` sur chaque lot d'entités.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Écrire une fonction `process(table, ctx)` : `table` est un lot d'entités (pyarrow.Table ; la géométrie est la colonne `geometry`, en WKB). Elle renvoie une table (sortie « output »), ou un dictionnaire `{"output": t1, "Rejected": t2}` (pour rejeter, `reject(table, "motif")`), ou rien pour ne rien envoyer. Disponibles sans import : `pa` (pyarrow), `pc` (pyarrow.compute), `np`, `shapely`, `duckdb`, `reject`. `ctx` donne `ctx.info("…")`, `ctx.warning("…")` et `ctx.variables`. Le code est appelé lot après lot dans l'ordre d'arrivée ; un état peut se garder dans `ctx.state` (un dictionnaire). **Attention** : ce code s'exécute avec vos droits sur l'ordinateur, comme un plugin : ne l'utiliser que dans des flux de confiance. Le schéma de sortie n'est pas connu avant l'exécution.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Code | Définit `process(table, ctx)`. | `def process(table, ctx):
    """table : un lot d'entités (pyarrow.Table, géométrie WKB dans la colonne "geometry").
    Renvoyer une table, ou {"output": table, "Rejected": autre_table}."""
    return table
` |

### Compteur — `Counter` {#counter}

Ajoute un numéro d'ordre à chaque entité, dans l'ordre d'arrivée (par groupe si demandé).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sans groupe : 1, 2, 3… Avec « Regrouper par » : la numérotation repart de « Début » pour chaque valeur.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut créé | Nom de l'attribut qui reçoit le numéro. | `_count` |
| Début | Premier numéro attribué. | `1` |
| Pas | Écart entre deux numéros successifs. | `1` |
| Regrouper par | Vide : une seule numérotation ; sinon elle repart de « Début » pour chaque valeur. |  |

### Requête SQL sur les entrées — `ExecuteSql` {#executesql}

Exécute une requête SQL DuckDB sur une ou plusieurs entrées du flux (jointures, agrégats, fenêtres, spatial).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque entrée est une table dont le nom est celui du port : `SELECT p.nom, COUNT(*) AS n FROM input p GROUP BY p.nom`. Plusieurs entrées (liste « Entrées ») permettent des jointures : `SELECT a.*, b.valeur FROM a JOIN b ON a.code = b.code`. La géométrie est la colonne `geometry` (fonctions ST_… de DuckDB : ST_Area, ST_Intersects, ST_Buffer…) et revient dans le résultat avec le système de coordonnées de la première entrée. Une seule instruction SELECT ; ni lecture de fichier ni accès réseau. Le résultat sort sur « output ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Entrées | Noms des ports d'entrée (lettres, chiffres et _) ; chacun est une table de la requête. | `['input']` |
| Requête SQL | Une instruction SELECT (ou WITH … SELECT) DuckDB. | `SELECT * FROM input` |

### Trier — `OrderBy` {#orderby}

Trie les entités selon un ou plusieurs attributs (valeurs vides à la fin).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Le tri est stable : à clés égales, l'ordre d'arrivée est conservé. « number » convertit le texte en nombre (une valeur non convertible est une erreur) ; « text » compare les valeurs comme du texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Clés de tri | Le premier attribut prime, les suivants départagent les égalités. |  |

## Composants

### Entrée du composant — `ComponentInput` {#componentinput}

Définit une entrée du composant : les entités reçues par ce port arrivent ici.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une entrée du nœud. Pour tester le composant seul, indiquer un fichier d'exemple : il n'est lu que si le flux est lancé directement (jamais quand il sert de composant).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Nom du port |  | `input` |
| Fichier d'exemple | Pour tester le composant seul. |  |

### Sortie du composant — `ComponentOutput` {#componentoutput}

Définit une sortie du composant : les entités reçues ici sortent par ce port.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une sortie du nœud. Lancé seul, le nœud laisse simplement passer les entités (utile pour les inspecter).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Nom du port |  | `output` |

## Entrées / sorties

### Appeler une API web — `HttpRequest` {#httprequest}

Appelle une adresse web (GET, POST…) pour chaque entité et range la réponse dans un attribut.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

L'adresse, les en-têtes et le corps peuvent citer des attributs de l'entité avec {attribut} (dans l'adresse, la valeur est encodée). Exemple : https://api-adresse.data.gouv.fr/search/?q={adresse}&limit=1. Les entités dont la réponse a un code inférieur à 400 sortent sur « output » avec le corps de la réponse (attribut `_http_body`) et le code (`_http_status`) ; les autres sortent sur « Rejected » avec le motif (`_rejection_reason`). Les appels se font en parallèle (« Appels simultanés ») avec un débit maximal par seconde, et sont repris en cas d'erreur réseau, de code 429 ou 5xx. Le proxy du poste (HTTPS_PROXY) est utilisé. Pour lire le JSON reçu : « Extraire du JSON ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Adresse | http:// ou https:// ; {attribut} pour une valeur de l'entité. |  |
| Méthode | Méthode HTTP. | `GET` |
| En-têtes | En-têtes ajoutés à chaque appel. |  |
| Corps | Corps de la requête (POST, PUT, PATCH) ; {attribut} accepté. |  |
| Attribut de la réponse | Reçoit le corps de la réponse (texte). | `_http_body` |
| Attribut du code | Reçoit le code HTTP (vide pour les erreurs réseau). | `_http_status` |
| Délai (s) | Délai maximal d'un appel. | `30.0` |
| Reprises | Nouvelles tentatives après une erreur réseau, un code 429 ou 5xx. | `2` |
| Appels simultanés | Nombre d'appels en parallèle. | `4` |
| Débit maximal (appels/s) | 0 = pas de limite. | `0.0` |

### Créer des entités — `CreateFeatures` {#createfeatures}

Crée une ou plusieurs entités de toutes pièces : attributs calculés et géométrie facultative.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Point de départ d'un flux qui n'a pas de fichier à lire : une seule entité pour déclencher des nœuds en aval (DatabaseQuery, HttpRequest, RunFlow…) ou plusieurs entités de test. Chaque attribut est une expression SQL qui peut utiliser `_index` (0, 1, 2…) ; les expressions suivantes voient les précédentes. Géométrie : « none », « point » (X et Y) ou « wkt » (texte WKT, ex. POLYGON((0 0, 1 0, 1 1, 0 0))). Le système de coordonnées s'applique à la géométrie.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Nombre d'entités | Combien d'entités créer. | `1` |
| Attributs | Calculés dans l'ordre, pour chaque entité. |  |
| Géométrie | none, point ou wkt. | `none` |
| X (point) | Expression SQL de X, ex. 1.5 ou 600000 + "_index". | `0` |
| Y (point) | Expression SQL de Y. | `0` |
| WKT | Géométrie WKT, identique pour toutes les entités. |  |
| Système de coordonnées | Celui de la géométrie. |  |

### SQL (créateur) — `DatabaseQuery` {#databasequery}

Exécute un ordre SQL sur une base (PostgreSQL, Oracle) et envoie les lignes qu'il renvoie.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le SQL est celui de la base de la connexion. Par défaut il est exécuté en **lecture seule** (SELECT). Cocher « Autoriser l'écriture » pour un UPDATE, un CREATE TABLE… (validé à la fin, annulé en cas d'erreur) ; un RETURNING (PostgreSQL) renvoie des lignes. Pour renvoyer une géométrie, la produire en WKB sous le nom « geometry » : ST_AsBinary(geom) AS geometry (PostGIS), SDO_UTIL.TO_WKBGEOMETRY(geom) AS "geometry" (Oracle) ; son système de coordonnées se règle dans « Système de coordonnées ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| SQL | Un seul ordre. |  |
| Autoriser l'écriture | Décoché : lecture seule (SELECT). Coché : UPDATE, CREATE TABLE… autorisés, validés à la fin. | false |
| Système de coordonnées | Si le résultat contient « geometry ». |  |

## Filtres

### Filtre par type de géométrie — `SplitByGeometryType` {#splitbygeometrytype}

Sépare les entités par type de géométrie : points, lignes, surfaces, collections, sans géométrie.

- **Entrées** : `input` — **Sorties** : `Point`, `Line`, `Area`, `Collection`, `NoGeometry` — **Traitement** : au fil de l'eau

Les multi-géométries suivent leur famille (MultiPolygon → Area). Une géométrie vide compte comme « NoGeometry ».

### Filtre spatial — `ExtractByLocation` {#extractbylocation}

Sépare les entités « candidate » selon leur relation spatiale avec les entités « filter ».

- **Entrées** : `candidate`, `filter` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Une entité candidate sort sur « Passed » si elle vérifie la relation (intersecte, est dans, contient, touche, à moins de N unités…) avec **au moins une** entité « filter », sinon sur « Failed ». Les entités sans géométrie partent sur « Rejected ». Les deux entrées doivent avoir le même système de coordonnées. « Inverser » échange Passed et Failed.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Relation | candidate <relation> filter (« within » : candidate est dans filter). | `intersects` |
| Distance (dwithin) | Unités du système de coordonnées. | `0.0` |
| Inverser Passed et Failed | Échange les sorties Passed et Failed. | false |

### Filtre à plusieurs sorties — `RouteByExpression` {#routebyexpression}

Répartit les entités sur plusieurs sorties selon des conditions ordonnées.

- **Entrées** : `input` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : au fil de l'eau

Chaque ligne donne une sortie et sa condition (expression SQL booléenne). Avec « first », une entité part sur la sortie de la **première** condition qu'elle vérifie ; avec « all », sur toutes celles qu'elle vérifie (elle est alors dupliquée). Celles qui ne vérifient aucune condition partent sur « Failed ». Une condition nulle compte comme fausse ; une entité dont l'évaluation échoue part sur « Rejected » avec le motif. Les sorties du nœud suivent la liste des conditions.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Conditions | Une sortie par ligne, dans l'ordre d'évaluation. | `[{'output': 'Passed', 'condition': ''}]` |
| Entité vérifiant plusieurs conditions | first : première sortie seulement ; all : toutes les sorties (l'entité est dupliquée). | `first` |

### Filtrer les doublons — `FindDuplicates` {#findduplicates}

Envoie la première entité de chaque clé sur « Unique », les suivantes sur « Duplicate ».

- **Entrées** : `input` — **Sorties** : `Unique`, `Duplicate` — **Traitement** : au fil de l'eau

Deux entités sont des doublons si toutes les clés sont égales (deux valeurs vides sont égales). Sans clé, on compare tous les attributs (hors géométrie). L'ordre d'arrivée décide de la première.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Clés | Vide = tous les attributs hors géométrie. |  |

### Filtrer par expression — `ExtractByExpression` {#extractbyexpression}

Envoie les entités sur « Passed » si elles vérifient les conditions, sinon sur « Failed ».

- **Entrées** : `input` — **Sorties** : `Passed`, `Failed`, `Rejected` — **Traitement** : au fil de l'eau

Chaque condition est une expression SQL booléenne : "pop" >= 1000 ; "nom" LIKE 'Saint%' ; "code" IN ('31', '09') ; ST_Area(geometry) > 500. Une condition nulle compte comme fausse. Une entité dont l'évaluation échoue part sur « Rejected » avec le motif.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Conditions | Expressions SQL booléennes, ex. "pop" >= 1000. |  |
| Combiner par | AND : toutes les conditions ; OR : au moins une. | `AND` |

### Échantillonner — `Sample` {#sample}

Prélève un échantillon : les N premières, les N dernières, une entité sur N ou N entités au hasard.

- **Entrées** : `input` — **Sorties** : `Sampled`, `Remaining` — **Traitement** : bloquant (lit toutes les données avant de produire)

« first » : les N premières entités reçues. « last » : les N dernières. « every » : une entité sur N (la première, puis la N+1-ième…). « random » : N entités tirées au hasard (graine fixe : le tirage se reproduit). Les entités retenues sortent sur « Sampled », les autres sur « Remaining », dans l'ordre d'arrivée. « first » et « every » travaillent au fil de l'eau ; « last » et « random » doivent lire toutes les entités.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Méthode | first, last, every (une sur N) ou random (au hasard). | `first` |
| N | Nombre d'entités (first, last, random) ou pas (every). | `100` |
| Graine (random) | Même graine, même tirage. | `0` |

## Géométrie

### Attribut vers géométrie — `GeometryFromAttribute` {#geometryfromattribute}

Construit la géométrie de chaque entité à partir d'un attribut texte (WKT, GeoJSON, WKB hexadécimal).

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Une valeur vide donne une entité sans géométrie ; une valeur illisible envoie l'entité sur « Rejected » avec le motif. Le système de coordonnées est celui indiqué (ou celui des données). Pour des coordonnées X et Y dans deux attributs : « Créer un point / un sommet ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut contenant la géométrie | Attribut texte qui contient la géométrie. |  |
| Format | Format du texte : wkt, geojson ou wkb_hex (WKB en hexadécimal). | `wkt` |
| Système de coordonnées | Système de coordonnées de la géométrie lue ; vide = celui des données. |  |
| Retirer l'attribut texte | Retire l'attribut texte une fois la géométrie créée. | false |

### Carroyage — `CreateGrid` {#creategrid}

Génère une grille de carrés ou d'hexagones sur une emprise (saisie, ou celle des entités reçues).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'emprise est saisie (X min, Y min, X max, Y max, dans le système de coordonnées choisi) ou, avec « Emprise des entités reçues », prise sur les géométries de l'entrée (le nœud a alors une entrée, et la grille reprend leur système de coordonnées). « Taille » est la largeur d'une cellule, dans l'unité du système (mètres en Lambert-93) : pour un hexagone, la distance entre deux côtés opposés. Chaque cellule reçoit son numéro (`cell_id`), sa colonne et sa ligne. Les hexagones débordent légèrement de l'emprise ; « Ne garder que les cellules qui touchent les entités » écarte les autres. Au plus 5 millions de cellules.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Emprise des entités reçues | Prend l'emprise sur les géométries de l'entrée. | false |
| X min | Bord gauche (emprise saisie). | `0.0` |
| Y min | Bord bas. | `0.0` |
| X max | Bord droit. | `1000.0` |
| Y max | Bord haut. | `1000.0` |
| Taille | Largeur d'une cellule, dans l'unité du système de coordonnées. | `100.0` |
| Forme | square ou hexagon. | `square` |
| Système de coordonnées | Celui de l'emprise saisie. |  |
| Ne garder que les cellules qui touchent les entités | Avec l'emprise des entités : écarte les cellules qui n'en rencontrent aucune. | false |

### Créer un point / un sommet — `PointFromXY` {#pointfromxy}

Crée un point depuis des attributs X/Y (Z), ou ajoute ce point comme sommet d'une ligne.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« point » : la géométrie devient le point (X, Y[, Z]). « append » : le point est ajouté à la fin de la ligne existante (une entité sans géométrie reçoit un simple point). Une coordonnée vide ou non numérique envoie l'entité sur « Rejected ». Le système de coordonnées est celui indiqué (ou celui des données).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut X (ou longitude) | Attribut contenant X (ou la longitude). |  |
| Attribut Y (ou latitude) | Attribut contenant Y (ou la latitude). |  |
| Attribut Z | Facultatif. |  |
| Action | point : la géométrie devient le point ; append : le point est ajouté à la fin de la ligne existante. | `point` |
| Système de coordonnées | Vide = celui des données. |  |

### Découper — `Clip` {#clip}

Découpe les entités « input » par les polygones « overlay » : parties dedans et dehors.

- **Entrées** : `input`, `overlay` — **Sorties** : `Inside`, `Outside`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

« union » : les polygones de découpe sont fusionnés ; chaque entité donne au plus une partie « Inside » (dans les polygones de découpe) et une partie « Outside ». « each » : une partie « Inside » par polygone de découpe qui la recouvre, avec les attributs choisis du polygone (préfixés) : c'est une intersection avec attributs. Les parties vides sont supprimées, comme les simples contacts (une parcelle qui touche le polygone de découpe par un bord reste entière dehors). Une entité entièrement dedans ou dehors sort telle quelle, sans calcul : seules les entités à cheval sont découpées. Les entités sans géométrie, ou que la découpe n'a pas pu traiter, partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Polygones de découpe | union : les polygones de découpe sont fusionnés (au plus une partie Inside par entité) ; each : une partie Inside par polygone de découpe qui recouvre l'entité. | `union` |
| Attributs des polygones à copier | Mode « each ». |  |
| Préfixe des attributs copiés | Ajouté devant le nom des attributs copiés du polygone de découpe (mode each). | `overlay_` |

### Enveloppe des géométries — `Hull` {#hull}

Remplace la géométrie par son enveloppe : convexe, concave, emprise, rectangle orienté ou cercle englobant.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« convex » : enveloppe convexe (l'élastique autour des points). « concave » : enveloppe qui épouse mieux la forme ; « Finesse » va de 1 (convexe) à 0 (très creusée). « envelope » : emprise (rectangle aligné sur les axes). « rectangle » : rectangle orienté de plus petite surface. « circle » : plus petit cercle englobant. Portée : « feature » calcule l'enveloppe de chaque entité ; « group » réunit les entités de chaque groupe en une seule (attributs de la première) ; « all » en fait une seule pour tout. Les entités sans géométrie partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Enveloppe | convex, concave, envelope, rectangle ou circle. | `convex` |
| Portée | feature : une par entité ; group : une par groupe ; all : une pour toutes les entités. | `feature` |
| Regrouper par | Pour la portée « group ». |  |
| Finesse (concave) | 1 = convexe, 0 = très creusée. | `0.3` |
| Trous autorisés (concave) | L'enveloppe concave peut avoir des trous. | false |

### Extraire des coordonnées — `ExtractCoordinates` {#extractcoordinates}

Écrit X, Y (et Z) d'un point caractéristique de la géométrie dans des attributs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Point utilisé : le point lui-même, le centre de gravité, un point garanti à l'intérieur de la surface, le premier ou le dernier sommet, ou le centre de l'emprise. Les coordonnées sont celles du système de la géométrie. « Emprise » ajoute `préfixe_minx`, `_miny`, `_maxx`, `_maxy`.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Point | Quel point lire : point (le point lui-même), centroid (centre de gravité), point_on_surface (toujours dedans), start / end (premier / dernier sommet), bbox_center (centre de l'emprise). | `centroid` |
| Préfixe des attributs | Ex. « pt_ » donne pt_x, pt_y. |  |
| Extraire aussi Z | Ajoute aussi l'altitude (z). | false |
| Ajouter l'emprise | Ajoute l'emprise : minx, miny, maxx, maxy (avec le préfixe). | false |

### Fusionner (dissoudre) — `Dissolve` {#dissolve}

Fusionne les polygones (ou lignes) de chaque groupe en une seule géométrie, sans frontières internes.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Une entité par groupe, avec les attributs de la première entité du groupe. Les polygones qui se touchent sont fusionnés ; les lignes sont réunies puis jointes bout à bout quand c'est possible. Les entités sans géométrie sont ignorées du calcul. Différent d'Aggregate, qui réunit sans fusionner.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Vide = tout fusionner. |  |
| Attribut du nombre d'entités | Vide = pas de comptage. | `_count` |

### Géométrie vers attribut — `GeometryToAttribute` {#geometrytoattribute}

Copie la géométrie de chaque entité dans un attribut texte (WKT, GeoJSON ou WKB hexadécimal).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Utile pour écrire une géométrie dans un CSV ou une colonne texte. « Retirer la géométrie » enlève ensuite la géométrie de l'entité. Une entité sans géométrie reçoit un attribut vide. Précision des coordonnées : laisser vide pour la précision complète.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut créé | Nom de l'attribut texte qui reçoit la géométrie. | `geometry_text` |
| Format | wkt, geojson ou wkb_hex (WKB en hexadécimal). | `wkt` |
| Décimales (WKT, GeoJSON) | Décimales conservées dans les coordonnées ; vide = précision complète. |  |
| Retirer la géométrie | Retire la géométrie de l'entité une fois copiée dans l'attribut. | false |

### Longueur de lignes — `MeasureLength` {#measurelength}

Ajoute la longueur de chaque ligne.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système (les coordonnées doivent alors être en longitude/latitude). Les entités qui ne sont pas des lignes (points, polygones, sans géométrie) partent sur « Rejected » ; pour le périmètre d'un polygone, utiliser « Surface et périmètre ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut longueur | Reçoit la longueur. | `_length` |
| Unité | Mètres ou kilomètres. | `m` |
| Méthode | planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur l'ellipsoïde WGS84, coordonnées en longitude/latitude. | `planar` |
| Décimales | Nombre de décimales conservées (0 à 15). | `2` |

### Remplacer par un point central — `Centroids` {#centroids}

Remplace chaque géométrie par un point : centre de gravité, point intérieur ou centre de l'emprise.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« centroid » : centre de gravité (peut tomber hors d'un polygone en forme de croissant) ; « point_on_surface » : un point toujours à l'intérieur ; « bbox_center » : centre de l'emprise. Les entités sans géométrie ou à géométrie vide partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Méthode | centroid : centre de gravité (peut sortir d'un polygone en croissant) ; point_on_surface : un point toujours à l'intérieur ; bbox_center : centre de l'emprise. | `centroid` |

### Reprojeter — `Reproject` {#reproject}

Transforme les géométries dans un autre système de coordonnées.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Le système d'origine est celui des données ; on peut le forcer si les données n'en déclarent pas (ou un faux). Les entités sans géométrie passent telles quelles ; une géométrie dont la transformation échoue (hors du domaine de validité, valeurs infinies) part sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Système cible | Ex. EPSG:2154. |  |
| Système source | Vide = celui des données. |  |

### Réparer les géométries — `FixGeometries` {#fixgeometries}

Rend valides les géométries invalides (auto-intersection, anneau non fermé…) ; les autres sont intactes.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

Toutes les entités ressortent sur « output » avec une géométrie valide. Les géométries déjà valides ne sont pas touchées. Les autres sont corrigées (GEOS make_valid) : un polygone en « nœud papillon » devient un multipolygone de deux triangles, un anneau mal fermé est refermé. « Garder le type » écarte les morceaux dégénérés que la réparation peut produire (un segment ou un point né d'un polygone aplati) : le polygone reste un polygone. « Rejected » reçoit, avec le motif, les entités sans géométrie, à géométrie vide ou qui ne donnent plus rien une fois réparées. Pour seulement contrôler sans modifier, utiliser « Valider les géométries ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Méthode | linework : les lignes sont découpées aux intersections (comportement classique, rapide). structure : reconstruit les surfaces d'après leurs anneaux (trous mieux respectés, plus lent). | `linework` |
| Garder le type de géométrie | Écarte les morceaux de dimension inférieure (lignes ou points nés d'un polygone aplati). | true |
| Attribut du motif de réparation | Reçoit le défaut d'origine pour les géométries réparées, vide pour les autres. Vide = pas d'attribut. | `_repaired_reason` |

### Simplifier les géométries — `Simplify` {#simplify}

Réduit le nombre de sommets en gardant la forme à la tolérance près (Douglas-Peucker).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

La tolérance est dans l'unité du système de coordonnées. « Préserver la topologie » évite les auto-intersections et les polygones qui disparaissent (un peu plus lent).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Tolérance | Écart maximal toléré, dans l'unité du système de coordonnées (mètres en Lambert-93). | `1.0` |
| Préserver la topologie | Évite les auto-intersections et les polygones qui disparaissent (un peu plus lent). | true |

### Superposition de polygones — `OverlayPolygons` {#overlaypolygons}

Découpe des polygones qui se chevauchent en morceaux disjoints, avec le nombre de polygones qui les recouvrent.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Tous les polygones reçus sont superposés : leurs contours découpent le plan en morceaux, et chaque morceau sort sur « output » avec `_overlap_count` (le nombre de polygones d'origine qui le recouvrent), `_origins` (leurs numéros, de 0, dans l'ordre d'arrivée) et, pour chaque attribut de « Attributs à lister », la liste des valeurs des polygones qui le recouvrent. Les morceaux qu'aucun polygone ne recouvre (trous, vide autour) ne sont pas produits. « Recouvrement minimal » ne garde que les morceaux recouverts au moins N fois (2 = les zones de conflit). Les entités qui ne sont pas des polygones partent sur « Rejected ». Toutes les entités sont lues avant le calcul.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs à lister | Pour chaque morceau : la liste des valeurs de ces attributs sur les polygones qui le recouvrent. |  |
| Recouvrement minimal | Ne garde que les morceaux recouverts au moins ce nombre de fois. | `1` |
| Attribut du nombre | Nombre de polygones qui recouvrent le morceau. | `_overlap_count` |
| Attribut des origines | Numéros (de 0) des polygones qui recouvrent le morceau ; vide = aucun. | `_origins` |

### Surface et périmètre — `MeasureArea` {#measurearea}

Ajoute la surface (et au choix le périmètre) de chaque polygone.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système (les coordonnées doivent alors être en longitude/latitude). Les entités qui ne sont pas des polygones partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut surface | Vide = pas de surface. | `_area` |
| Attribut périmètre | Vide = pas de périmètre. |  |
| Unité de surface | Unité de la surface écrite : m² (m2), hectares (ha) ou km² (km2). | `m2` |
| Unité de périmètre | Unité du périmètre écrit : mètres ou kilomètres. | `m` |
| Méthode | planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur l'ellipsoïde WGS84, les coordonnées doivent être en longitude/latitude. | `planar` |
| Décimales | Nombre de décimales conservées (0 à 15). | `2` |

### Valider les géométries — `CheckValidity` {#checkvalidity}

Sépare les géométries valides des invalides (auto-intersection, anneau non fermé…).

- **Entrées** : `input` — **Sorties** : `Valid`, `Invalid` — **Traitement** : au fil de l'eau

Les entités valides sortent sur « Valid », les autres sur « Invalid » avec le motif dans `_invalid_reason`. Géométrie vide ou absente : « Invalid » (sauf si décoché). Pour corriger les géométries invalides, utiliser « Réparer les géométries ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Refuser les géométries vides ou absentes | Coché : une géométrie vide ou absente va sur « Invalid » ; décoché : elle passe sur « Valid ». | true |

### Zone tampon — `Buffer` {#buffer}

Remplace chaque géométrie par une zone tampon (buffer) de la distance indiquée.

- **Entrées** : `input` — **Sorties** : `output`, `Rejected` — **Traitement** : au fil de l'eau

La distance est dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système géographique (degrés). Elle peut venir d'un attribut. Une distance négative rétrécit un polygone (résultat vide = entité sur « Rejected »). Les entités sans géométrie ou à distance invalide partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Distance | Dans l'unité du système de coordonnées (mètres en Lambert-93). Négative : rétrécit un polygone. | `10.0` |
| Distance depuis un attribut | Remplace la distance fixe. |  |
| Extrémités | Forme du bout des lignes : round (arrondi), flat (plat), square (carré). | `round` |
| Angles | Forme des angles : round (arrondi), mitre (angle vif), bevel (coupé). | `round` |
| Segments par quart de cercle | Finesse des arrondis : segments par quart de cercle. | `16` |

### Éclater les géométries multiples — `MultipartToSingleparts` {#multiparttosingleparts}

Sépare chaque géométrie multiple (MultiPolygon, MultiLineString…) en une entité par partie.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Une entité dont la géométrie a N parties donne N entités qui recopient les autres attributs. Les géométries simples, vides ou absentes passent telles quelles. « Éclater aussi les collections imbriquées » descend dans les GeometryCollection qui contiennent d'autres collections. « Attribut du numéro » reçoit le rang de la partie (à partir de 0) et « Attribut du nombre » le nombre de parties de l'entité d'origine.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut du numéro | Rang de la partie, de 0 ; vide = aucun. | `_part` |
| Attribut du nombre | Nombre de parties de l'entité d'origine ; vide = aucun. |  |
| Éclater aussi les collections imbriquées | Descend dans les collections de collections. | true |

## Jointures

### Jointure attributaire — `JoinByField` {#joinbyfield}

Ajoute aux entités de « base » les attributs des entités de « join » qui ont la même clé.

- **Entrées** : `base`, `join` — **Sorties** : `Joined`, `UnjoinedBase`, `UnjoinedJoin` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités de « base » avec correspondance sortent sur « Joined » ; les autres sur « UnjoinedBase ». Les entités de « join » qui ne correspondent à aucune entité de « base » sortent sur « UnjoinedJoin ». Une clé nulle ne correspond jamais. Si les clés n'ont pas le même type (entier et texte), la comparaison se fait en texte. La géométrie du résultat est celle de « base », celle de « join » ou aucune, au choix.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Clés de « base » | Attributs de « base » qui font le lien avec « join », dans le même ordre que les clés de « join ». |  |
| Clés de « join » | Dans le même ordre que « base ». |  |
| Attributs à ajouter | Vide = tous ceux de « join » hors clés. |  |
| Préfixe des attributs ajoutés | Évite les conflits de noms. |  |
| Si le nom existe déjà | other : la valeur de « join » ; base : on garde l'existante. | `other` |
| Plusieurs correspondances | all : une entité par correspondance ; first ; error. | `all` |
| Garder les non-appariées dans Joined | Jointure « gauche » : attributs vides. | false |
| Géométrie du résultat | Celle de « base », celle de « join » (vide pour une entité gardée sans correspondance), ou aucune. | `base` |

### Plus proches voisines — `JoinByNearest` {#joinbynearest}

Pour chaque entité « base », trouve ses k plus proches entités parmi les « candidate » et recopie leurs attributs.

- **Entrées** : `base`, `candidate` — **Sorties** : `Matched`, `NoNeighbor`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque « base » donne au plus k entités sur « Matched » (une par voisine, de la plus proche à la plus éloignée) : les attributs de la base, ceux de la voisine (préfixés), son rang (`_rank`, de 1) et la distance (`_distance`, dans l'unité du système de coordonnées). Une base sans voisine à moins de « Distance maximale » sort sur « NoNeighbor ». La distance est celle qui sépare les géométries (le bord à bord pour des polygones), pas celle des centres. Les deux entrées doivent avoir le même système de coordonnées ; une entité sans géométrie part sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Nombre de voisines (k) | Combien de plus proches voisines chercher. | `1` |
| Distance maximale | Dans l'unité du système de coordonnées ; 0 = sans limite. | `0.0` |
| Attributs de la voisine | Vide = tous ceux de « candidate » hors géométrie. |  |
| Préfixe | Ajouté devant les attributs de la voisine. | `near_` |
| Attribut du rang | Rang de la voisine, de 1. | `_rank` |
| Attribut de la distance | Distance à la voisine. | `_distance` |

### Point dans polygone — `PointsInPolygons` {#pointsinpolygons}

Ajoute à chaque point les attributs du polygone qui le contient (jointure spatiale).

- **Entrées** : `points`, `areas` — **Sorties** : `Inside`, `Outside`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les points dans un polygone sortent sur « Inside » avec les attributs choisis du polygone (tous par défaut), les autres sur « Outside ». Si plusieurs polygones contiennent le point : « first » garde le premier, « all » produit une entité par polygone, « error » refuse. Un point sur la frontière compte comme dedans. Les entités sans géométrie partent sur « Rejected ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attributs du polygone à copier | Vide = tous hors géométrie. |  |
| Préfixe des attributs copiés | Ajouté devant le nom des attributs copiés du polygone. |  |
| Plusieurs polygones | Si plusieurs polygones contiennent le point — first : le premier ; all : une entité par polygone ; error : refus. | `first` |

### Relateur spatial — `JoinByLocation` {#joinbylocation}

Ajoute à chaque entité « base » le nombre d'entités « related » en relation avec elle, et au choix la somme ou la liste de leurs attributs, ou leurs attributs eux-mêmes.

- **Entrées** : `base`, `related` — **Sorties** : `output`, `Rejected` — **Traitement** : bloquant (lit toutes les données avant de produire)

Pour chaque entité de « base », on compte les entités de « related » qui vérifient la relation (base <relation> related), 0 s'il n'y en a aucune. Options : additionner des attributs numériques des entités liées (« <attribut>_sum ») ; lister les valeurs d'un attribut (séparées par un séparateur, sans doublon, triées) ; récupérer des attributs des entités liées, comme une jointure attributaire (préfixe, conflit de noms) : ceux de la première entité liée, une entité « base » par entité liée, ou la liste des valeurs de chaque attribut ; vides sans entité liée. Compter des points dans des polygones : relation intersects, polygones sur « base », points sur « related » ; un point sur une limite commune compte dans les deux polygones, ou seulement dans le premier avec « first ». Sans liste, les entités « related » passent lot par lot sans être gardées en mémoire : des millions de points se comptent sans peine. Les entités « base » sans géométrie partent sur « Rejected » ; les entités « related » sans géométrie ne comptent nulle part.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Relation | base <relation> related. | `intersects` |
| Distance (dwithin) | Distance maximale pour la relation dwithin, dans l'unité du système de coordonnées. | `0.0` |
| Attribut du nombre d'entités liées | Nom de l'attribut qui reçoit le nombre d'entités de « related » vérifiant la relation. | `_related_count` |
| Attributs à additionner | Attributs numériques de « related » : leur somme par entité « base » dans « <attribut>_sum ». |  |
| Entité liée à plusieurs entités « base » | all : comptée pour chacune ; first : pour la première seulement (la somme des comptes égale alors le nombre d'entités liées). | `all` |
| Lister cet attribut des entités liées | Vide = pas de liste. |  |
| Nom de l'attribut de liste | Nom de l'attribut qui reçoit la liste (si « Lister cet attribut » est renseigné). | `_related_values` |
| Séparateur | Texte placé entre deux valeurs de la liste. | `, ` |
| Attributs à récupérer | Attributs de « related » ajoutés à l'entité « base », comme une jointure attributaire. Vide = aucun. |  |
| Plusieurs entités liées | Pour les attributs récupérés. first : ceux de la première entité liée (ordre de « related ») ; all : une entité « base » par entité liée ; list : les valeurs distinctes de chaque attribut, jointes par le séparateur. | `first` |
| Préfixe des attributs récupérés | Évite les conflits de noms. |  |
| Si le nom existe déjà | other : la valeur de « related » ; base : on garde celle de « base ». | `other` |

### SQL (par entité) — `DatabaseQueryPerFeature` {#databasequeryperfeature}

Exécute un ordre SQL pour chaque entité, en y injectant ses attributs ; ajoute le résultat.

- **Entrées** : `input` — **Sorties** : `output`, `NoResult` — **Traitement** : au fil de l'eau

Écrire `{{attribut}}` dans le SQL : la valeur de l'entité est transmise comme **paramètre lié** (jamais collée dans le texte : pas d'injection SQL). Ex. SELECT nom FROM communes WHERE code = {{insee}}. « first » ajoute les colonnes de la première ligne du résultat ; « all » produit une entité par ligne. Une entité sans résultat part sur « NoResult ». Lecture seule sauf « Autoriser l'écriture » (tout est validé à la fin, annulé en cas d'erreur). Une requête par entité : prévoir de la jointure SQL si le volume est important.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| SQL | Avec {{attribut}} pour les valeurs de l'entité. |  |
| Lignes du résultat | first : les colonnes de la première ligne du résultat sont ajoutées à l'entité ; all : une entité par ligne du résultat. | `first` |
| Préfixe des colonnes ajoutées | Ajouté devant le nom des colonnes ajoutées. |  |
| Autoriser l'écriture | Décoché : lecture seule (SELECT). Coché : UPDATE, INSERT… autorisés, validés à la fin. | false |

## Lecture

### Lecture CSV — `CsvReader` {#csvreader}

Lit un fichier CSV (ou texte délimité) ; une colonne WKT ou des colonnes X/Y donnent la géométrie.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les colonnes sont lues comme du texte (les codes à zéro initial sont conservés), sauf si « Détecter les types » est coché. Indiquer la colonne WKT, ou les colonnes X et Y, pour obtenir une géométrie, avec son système de coordonnées. Un motif (dossier/*.csv) lit tous les fichiers : leurs colonnes sont réunies par nom.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Séparateur | Vide = détection automatique. |  |
| Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| Première ligne = en-têtes | Décocher si le fichier n'a pas de ligne d'en-têtes. | true |
| Détecter les types | Sinon tout est lu en texte : les codes à zéro initial sont conservés. | false |
| Colonne WKT | Colonne contenant la géométrie en WKT. |  |
| Colonne X | Colonne des X (ou longitudes), avec la colonne Y. |  |
| Colonne Y | Colonne des Y (ou latitudes), avec la colonne X. |  |
| Système de coordonnées | Celui de la colonne WKT ou des X/Y. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture Excel — `ExcelReader` {#excelreader}

Lit une feuille d'un classeur Excel (.xlsx).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

La première ligne de la plage donne les noms de colonnes (sauf si décoché). Vide : la première feuille.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Feuille | Nom de la feuille ; vide = la première. |  |
| Plage | Ex. A1:F200 ; vide = toute la feuille. |  |
| Première ligne = en-têtes | Décocher si la plage n'a pas de ligne d'en-têtes. | true |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture FlatGeobuf — `FlatGeobufReader` {#flatgeobufreader}

Lit un fichier FlatGeobuf (.fgb).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Format vecteur compact et indexé, rapide à lire.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture GeoJSON — `GeoJsonReader` {#geojsonreader}

Lit un fichier GeoJSON.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le GeoJSON est en WGS 84 (EPSG:4326) selon la norme ; un autre système déclaré dans le fichier est respecté.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture GeoPackage — `GeoPackageReader` {#geopackagereader}

Lit une couche d'un GeoPackage (.gpkg).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Indiquer la couche si le GeoPackage en contient plusieurs (vide : la première).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Couche | Nom de la couche ; vide = la première. |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture (Geo)Parquet — `GeoParquetReader` {#geoparquetreader}

Lit un fichier Parquet, avec ou sans géométrie (GeoParquet), sur le disque ou à une adresse web (https://).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Un Parquet sans géométrie est lu tel quel. Pour un GeoParquet, la géométrie et son système de coordonnées sont lus dans ses métadonnées. Un fichier seul, local, sans nom du fichier ajouté ni limite de lignes, n'est pas recopié : le résultat du nœud est le fichier lui-même (le modifier ensuite oblige à relancer le flux). Une adresse https:// est lue par plages : seules les parties utiles du fichier sont téléchargées (proxy des Paramètres).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier, motif (*.ext, dossier/**/*.ext : tous les fichiers trouvés) ou adresse web https://. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture MapInfo — `MapInfoReader` {#mapinforeader}

Lit une table MapInfo (.tab) ou un fichier MIF/MID (.mif).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le format (TAB ou MIF) est déduit de l'extension.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |

### Lecture Oracle — `OracleReader` {#oraclereader}

Lit une table, une vue ou une requête Oracle ; la colonne SDO_GEOMETRY devient la géométrie du flux.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Choisir une table (schéma puis table) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est exécuté par le serveur. Le système de coordonnées est le SRID des géométries (SDO_SRID). Oracle range en majuscules les noms saisis sans guillemets : les colonnes lues sont en majuscules (NOM, GEOM).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| Table ou vue | Table ou vue lue en entier. Ignorée si une requête est renseignée. |  |
| Requête SQL | Facultatif : remplace la table ; un seul SELECT. |  |
| Filtre (WHERE) | Condition SQL ajoutée en WHERE, évaluée par le serveur ; vide = aucun filtre. |  |
| Colonne géométrique | Vide = la première trouvée. |  |
| Système de coordonnées | Vide = SRID de la colonne. |  |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture PostgreSQL / PostGIS — `PostgresReader` {#postgresreader}

Lit une table, une vue ou une requête PostgreSQL ; la géométrie PostGIS devient la géométrie du flux.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Choisir une table (schéma puis table, dans les listes lues sur la connexion) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est exécuté par le serveur, donc seules les lignes utiles circulent. La lecture se fait en lecture seule, par curseur serveur (mémoire bornée). Le système de coordonnées est celui de la colonne (SRID). Les « numeric » sont lus en flottant ; les types non gérés (intervalles, hstore…) en texte.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| Table ou vue | Table ou vue à lire en entier (équivaut à SELECT * FROM table). Ignorée si une requête est renseignée. |  |
| Requête SQL | Facultatif. Vide : la table est lue en entier (SELECT * FROM table). Renseignée : remplace la table ; un seul SELECT (un « ; » final est toléré). |  |
| Filtre (WHERE) | Condition SQL ajoutée en WHERE, évaluée par le serveur ; vide = aucun filtre. |  |
| Colonne géométrique | Vide = la première trouvée. |  |
| Système de coordonnées | Vide = SRID de la colonne. |  |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture SQLite / SpatiaLite — `SpatiaLiteReader` {#spatialitereader}

Lit une table d'une base SQLite ou SpatiaLite (.sqlite, .db), ou le résultat d'une requête SQL.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Indiquer la table (vide : la première) **ou** une requête SQL (dialecte SQLite), qui la remplace. La géométrie SpatiaLite devient la géométrie du flux. Pour un GeoPackage, utiliser « Lecture GeoPackage ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Table | Nom de la table ; vide = la première. |  |
| Requête SQL | Facultatif : remplace la table (dialecte SQLite) ; un seul SELECT. |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

### Lecture Shapefile — `ShapefileReader` {#shapefilereader}

Lit un Shapefile (.shp et ses fichiers compagnons).

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

L'encodage est lu dans le fichier .cpg s'il existe ; sinon utf-8, puis latin-1.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés. |  |
| Encodage | Vide = détection automatique (utf-8, sinon latin-1). |  |
| Filtre attributaire | Condition SQL appliquée à la lecture ; vide = toutes les entités. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles). |  |
| Ajouter le nom du fichier | Ajoute la colonne « filename » (utile avec un motif de fichiers). | false |
| Lignes lues au plus | Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes. | `0` |

## Lots et variables

### Lancer un flux — `RunFlow` {#runflow}

Exécute un autre flux pour chaque entité reçue, avec des paramètres tirés de ses attributs.

- **Entrées** : `input` — **Sorties** : `Succeeded`, `Failed` — **Traitement** : bloquant (lit toutes les données avant de produire)

Chaque entité reçue lance une exécution du flux indiqué (`etl run` dans un processus séparé : mémoire isolée, un plantage n'arrête pas les autres). Les paramètres publiés du flux lancé reçoivent la valeur d'un attribut de l'entité (ou une valeur fixe). Les entités dont le flux a réussi sortent sur « Succeeded », les autres sur « Failed » ; dans les deux cas elles gardent leurs attributs et reçoivent _flow_status, _flow_seconds et _flow_error. « En parallèle » règle le nombre d'exécutions simultanées. Pour un dossier de fichiers : ListFiles → RunFlow, avec le paramètre du flux lancé relié à l'attribut path.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Flux à lancer | Fichier .json du flux. |  |
| Paramètres | Valeur de chaque paramètre publié du flux lancé. |  |
| En parallèle | Exécutions simultanées (1 à 16). | `1` |
| S'arrêter au premier échec | Coché : les exécutions restantes ne sont pas lancées après un échec. | false |
| Garder les données | Garde le cache de chaque exécution (consomme du disque). | false |

### Lire les fichiers listés — `ReadListedFiles` {#readlistedfiles}

Lit le fichier désigné par chaque entité reçue et envoie leurs entités.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque entité reçue donne un chemin de fichier (attribut « Chemin ») ; le fichier est lu avec les mêmes réglages que le lecteur de son format et ses entités sont envoyées. Les schémas des fichiers sont réunis par nom d'attribut : un attribut absent d'un fichier est vide, deux types incompatibles deviennent du texte. Pour que le schéma soit connu avant l'exécution (formulaires des nœuds suivants), indiquer un fichier d'exemple. « Attributs à recopier » ajoute à chaque entité lue des attributs de l'entité reçue (par exemple stem, le nom du fichier sans extension).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Chemin | Attribut qui contient le chemin du fichier à lire. | `path` |
| Fichier d'exemple | Facultatif : donne le schéma avant l'exécution, pour les formulaires des nœuds suivants. |  |
| Attributs à recopier | Attributs de l'entité reçue ajoutés à chaque entité lue (un nom déjà présent est refusé). |  |
| Format | Déduit de l'extension si « auto ». | `auto` |
| Couche / feuille | Vide = la première. |  |
| Plage (Excel) | Ex. A1:F200. |  |
| Encodage | Vide = détection automatique. |  |
| Filtre attributaire | Condition SQL appliquée à la lecture (GDAL). |  |
| Séparateur (CSV) | Vide = détection automatique. |  |
| Première ligne = en-têtes (CSV, Excel) | Décocher si pas de ligne d'en-têtes. | true |
| Détecter les types (CSV) | Sinon tout est lu en texte. | false |
| Ajouter le nom du fichier | Ajoute la colonne « filename ». | false |
| Colonne WKT (CSV) | Colonne qui contient la géométrie en WKT. |  |
| Colonne X (CSV) | À renseigner avec la colonne Y. |  |
| Colonne Y (CSV) | À renseigner avec la colonne X. |  |
| Système de coordonnées (CSV) | Celui de la colonne WKT ou de X/Y. |  |
| Fichier illisible | error : arrête le flux ; skip : ignore le fichier avec un avertissement. | `error` |

### Lister des fichiers — `ListFiles` {#listfiles}

Une entité par fichier d'un dossier (ou d'un fichier liste) : chemin, nom, taille, date.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sert à traiter des dossiers : relier à « Lire les fichiers listés » (ReadListedFiles) pour lire chacun, ou à « Lancer un flux » (RunFlow) pour exécuter un flux par fichier. Attributs : path (chemin complet), dir, name (avec extension), stem (sans extension), ext (avec le point, en minuscules), size (octets), mtime (dernière modification), is_dir. Les motifs sont ceux du shell (*.shp, commune_??.gpkg) et s'appliquent au nom ; plusieurs motifs s'additionnent. Un fichier liste (.txt) contient un chemin par ligne ; un CSV, une colonne « path » (ou, à défaut, la première colonne).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Source | folder : un dossier ; list_file : un fichier qui liste les chemins. | `folder` |
| Dossier | Dossier à parcourir (source folder). |  |
| Motifs | Motifs de nom de fichier, ex. *.shp ; vide = tous les fichiers. | `['*']` |
| Parcourir les sous-dossiers | Descend dans les sous-dossiers. | false |
| Lister aussi les dossiers | Ajoute une entité par dossier (is_dir = vrai). | false |
| Fichier liste | Fichier .txt (un chemin par ligne) ou .csv (colonne path). |  |
| Fichier introuvable dans la liste | error : arrête le flux ; skip : l'ignore avec un avertissement. | `error` |
| Trier par | Ordre des entités : chemin, date de modification ou taille. | `path` |

### Poser des variables — `SetVariables` {#setvariables}

Lit une valeur dans les données (un attribut, ou le nombre d'entités) et la pose en variable.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les entités passent sans changement. Les variables posées ici s'utilisent dans les nœuds situés **en aval** avec ${var:NOM}, dans n'importe quel paramètre : un nœud démarre quand tout son amont est terminé, donc la valeur est connue. L'analyse refuse une variable que rien ne pose en amont. Exemple : « Agréger » calcule la date la plus récente, SetVariables la pose en DATE_MAX, une lecture PostgreSQL filtre sur "date" > '${var:DATE_MAX}'.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Variables | Une ligne par variable posée. |  |

### Écrire un fichier par groupe — `SplitToFiles` {#splittofiles}

Écrit les entités dans plusieurs fichiers : le chemin se compose avec les valeurs d'attributs.

- **Entrées** : `input` — **Sorties** : `summary` — **Traitement** : au fil de l'eau

Écrire {attribut} dans le chemin crée un fichier par valeur de cet attribut, ex. D:\sorties\{departement}\communes.gpkg ou {departement}_{annee}.csv. Les caractères interdits dans un nom de fichier sont remplacés par _ et une valeur vide donne « _vide ». Les réglages du format sont ceux de l'écriture de ce format. Chaque fichier est ouvert une seule fois : au plus 200 fichiers à la fois. La sortie « summary » donne une entité par fichier écrit (chemin, nombre d'entités, format), pour la suite du flux (journal, SetVariables, RunFlow…).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Chemin du fichier | Chemin avec des {attributs} entre accolades ; dossiers créés si besoin. |  |
| Format | Déduit de l'extension si « auto ». | `auto` |
| Couche / feuille | GeoPackage, MapInfo… : couche ; Excel : feuille. |  |
| Mode | create : échoue si un fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
| Encodage | Vide = celui du format (utf-8). |  |
| Séparateur (CSV) | Séparateur de colonnes du CSV écrit. | `,` |
| Compression ((Geo)Parquet) | Compression des fichiers (Geo)Parquet. | `zstd` |
| Partitionner par ((Geo)Parquet) | Colonnes dont chaque valeur donne un sous-dossier de chaque fichier. |  |

## Rasters

### Analyse de terrain (pente, exposition, ombrage) — `TerrainAnalysis` {#terrainanalysis}

Calcule la pente, l'exposition, l'ombrage ou la rugosité d'un modèle numérique de terrain.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le raster d'entrée est un MNT (altitudes). Produits : slope (pente, en degrés ou en pourcentage), aspect (exposition en degrés depuis le nord, dans le sens horaire ; -1 sur un terrain plat), hillshade (ombrage de 0 à 255, selon « Azimut » et « Hauteur du soleil »), roughness (écart entre un pixel et ses voisins). Chaque produit devient une bande. Les dérivées utilisent la méthode de Horn (8 voisins). « Facteur Z » convertit les altitudes dans l'unité horizontale (1 si les deux sont en mètres). En degrés (WGS84), la taille des pixels est convertie en mètres à la latitude du pixel. Les pixels du bord et ceux dont un voisin est sans donnée restent sans donnée.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Produits | slope, aspect, hillshade ou roughness : une bande chacun. | `['slope']` |
| Bande | Bande des altitudes (à partir de 1). | `1` |
| Unité de pente | degrees ou percent. | `degrees` |
| Facteur Z | Multiplie les altitudes (1 si tout est en mètres). | `1.0` |
| Azimut (ombrage) | Direction du soleil, en degrés depuis le nord. | `315.0` |
| Hauteur du soleil (ombrage) | Angle du soleil au-dessus de l'horizon. | `45.0` |

### Calculatrice de rasters — `RasterCalculator` {#rastercalculator}

Calcule de nouvelles bandes par expressions sur les bandes d'un ou plusieurs rasters (NDVI, masques, seuils).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque ligne crée une bande du raster résultat. Dans l'expression, les bandes s'appellent b1, b2… ou par leur nom (red, nir…) : (nir - red) / (nir + red). Opérateurs + - * / ** % ; comparaisons < <= == != >= > ; logiques & | ~ ; fonctions where(condition, si_vrai, si_faux), abs, sqrt, exp, log, log10, sin, cos, tan, arctan2, minimum, maximum… (calcul vectorisé et multithread, sans exécution de code). Un pixel sans donnée dans une bande utilisée reste sans donnée dans le résultat. Plusieurs entrées (liste « Entrées ») : celles qui suivent la première sont rééchantillonnées sur sa grille, et leurs bandes s'écrivent port_b1 ou port_nom (a_b1, b_red) ; avec une seule ligne dans une autre entrée, ce raster sert pour tous ceux de la première. Résultat en float32 par défaut.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Entrées | Noms des ports d'entrée (lettres, chiffres et _). | `['input']` |
| Bandes calculées | Une ligne par bande du résultat. |  |
| Type du résultat | Type des pixels du résultat. | `float32` |
| Valeur sans donnée | Vide = NaN pour un résultat décimal, le maximum du type sinon. |  |
| Rééchantillonnage | Pour aligner les entrées suivantes sur la première. | `bilinear` |

### Courbes de niveau — `Contour` {#contour}

Trace les courbes de niveau d'un modèle numérique de terrain : une ligne par courbe, avec son altitude.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Une courbe est tracée tous les « Équidistance » (10 m : 100, 110, 120…), à partir de « Altitude de base », ou pour les altitudes de « Niveaux » si la liste est renseignée. Chaque ligne porte son altitude (« Attribut de l'altitude ») et les attributs du raster. « Longueur minimale » écarte les petites courbes (îlots). Les pixels sans donnée interrompent les courbes. Le raster est lu en mémoire (au plus 40 millions de pixels : découper d'abord au-delà). Les lignes sont dans le système du raster.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bande | Bande des altitudes (à partir de 1). | `1` |
| Équidistance | Écart d'altitude entre deux courbes. | `10.0` |
| Altitude de base | Altitude à laquelle la série de courbes est ancrée. | `0.0` |
| Niveaux | Altitudes précises (remplacent l'équidistance). |  |
| Longueur minimale | Écarte les courbes plus courtes (unité du système). | `0.0` |
| Attribut de l'altitude | Reçoit l'altitude de la courbe. | `elevation` |

### Découpe de rasters — `ClipRasterByMask` {#cliprasterbymask}

Découpe chaque raster par des polygones (communes, parcelles) ou par une emprise ; les pixels hors du polygone sont masqués.

- **Entrées** : `input`, `mask` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Par polygones : la deuxième entrée (« mask ») donne les polygones, dans n'importe quel système (ils sont reprojetés). « union » : un seul raster par raster d'origine, découpé par la réunion des polygones ; « each » : un raster par polygone, avec ses attributs (« Attributs du masque à copier ») : par exemple un MNT par commune. « Recadrer » réduit le raster à l'emprise des polygones ; sinon il garde sa taille et seuls les pixels hors polygone sont masqués. « Pixels touchés » inclut tout pixel que le polygone touche, même un peu. Par emprise : Ouest, Sud, Est, Nord dans le système du raster. Un polygone qui ne recouvre pas le raster ne donne rien.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Découper par | features : polygones (entrée « mask ») ; extent : une emprise saisie. | `features` |
| Polygones | union : un résultat par raster ; each : un résultat par polygone. | `union` |
| Emprise | Ouest, Sud, Est, Nord dans le système du raster (par emprise). |  |
| Recadrer | Réduit le raster à l'emprise de la découpe. | true |
| Pixels touchés | Garde tout pixel que le polygone touche. | false |
| Attributs du masque à copier | Mode « each » : ajoutés aux rasters produits. |  |

### Empiler des bandes — `StackBands` {#stackbands}

Réunit plusieurs rasters (une bande B04, une bande B08…) en un seul raster multibande.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les rasters reçus (par groupe, avec « Regrouper par ») deviennent les bandes d'un seul raster, sur la grille du premier. Si tous partagent la même grille, le résultat est un raster virtuel (instantané, sans copie) ; sinon (résolutions ou emprises différentes) ils sont rééchantillonnés sur la grille du premier et écrits dans un fichier. « Bande retenue » : la première bande de chaque raster, ou toutes. Les noms des bandes viennent de « Attribut du nom » (par exemple le nom du fichier) ou, à défaut, de la liste « Noms » puis des noms d'origine.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Regrouper par | Un raster multibande par valeur ; vide = un seul pour tous. |  |
| Bande retenue | first : la première bande de chaque raster ; all : toutes ses bandes. | `first` |
| Attribut du nom | Attribut dont la valeur nomme la bande. |  |
| Noms | Noms des bandes dans l'ordre (sans attribut du nom). |  |
| Rééchantillonnage | Si les grilles diffèrent. | `bilinear` |

### Entités vers raster — `Rasterize` {#rasterize}

Transforme des polygones, lignes ou points en raster : valeur d'un attribut ou valeur fixe.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : bloquant (lit toutes les données avant de produire)

Les entités sont dessinées dans un raster de pixels de « Taille du pixel » (dans l'unité de leur système de coordonnées), sur leur emprise ou sur celle de « Emprise ». Chaque pixel prend la valeur de « Attribut de la valeur » de l'entité qui le recouvre (la dernière en cas de recouvrement, ou la somme avec « add »), ou la « Valeur fixe » (1 par défaut : un masque). Les pixels non recouverts valent « Valeur de fond » (sans donnée si vide). « Pixels touchés » dessine tout pixel que l'entité touche, utile pour des lignes fines. Le dessin se fait par fenêtres de 512 pixels : la taille du raster n'est limitée que par le disque.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Attribut de la valeur | Vide = valeur fixe. |  |
| Valeur fixe | Valeur des pixels recouverts si aucun attribut n'est choisi. | `1.0` |
| Taille du pixel | Dans l'unité du système de coordonnées des entités. | `10.0` |
| Emprise | Ouest, Sud, Est, Nord ; vide = l'emprise des entités. |  |
| Système de coordonnées | Vide = celui des entités. |  |
| Type | Type des pixels. | `float32` |
| Valeur de fond | Vide = sans donnée. |  |
| Pixels touchés | Dessine tout pixel touché par l'entité. | false |
| Recouvrements | replace : la dernière l'emporte ; add : somme. | `replace` |

### Lecture de rasters — `RasterReader` {#rasterreader}

Référence un ou plusieurs fichiers raster (GeoTIFF, COG, ASC, JP2, VRT…) : une entité par raster.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Les pixels ne sont pas copiés : chaque entité décrit un raster (fichier, taille, bandes, système de coordonnées) et porte son emprise comme géométrie, ce qui permet de les voir sur la carte, de les filtrer ou de les joindre. Le chemin accepte un fichier, un motif (D:/mnt/*.tif, **/*.asc), une liste (« Autres fichiers ») ou une adresse web https:// (COG distant, lu par plages sans tout télécharger). « Bandes » choisit des bandes (numéros à partir de 1, ou noms) ; « Noms des bandes » nomme celles du résultat (utilisés dans RasterCalculator). Les formats lus sont ceux de GDAL.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Fichier, motif (*.tif, **/*.asc) ou adresse https://. |  |
| Autres fichiers | Fichiers ou motifs lus en plus de « Fichier ». |  |
| Bandes | Numéros (à partir de 1) ou noms des bandes à garder ; vide = toutes. |  |
| Noms des bandes | Noms donnés aux bandes du résultat, dans l'ordre. |  |
| Ajouter le nom du fichier | Ajoute l'attribut « filename ». | true |

### Mosaïque et composite de rasters — `MergeRasters` {#mergerasters}

Assemble des rasters côte à côte (dalles d'un MNT), ou en fait un composite temporel (médiane de plusieurs dates).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

« spatial » : les rasters (même système de coordonnées, même taille de pixel, même nombre de bandes) sont posés sur leur emprise commune ; là où ils se recouvrent, le dernier qui a une donnée l'emporte (ordre de « Trier par »). Le résultat est un raster virtuel (VRT, instantané, sans copie) ou, avec « Écrire en GeoTIFF », un vrai fichier. « composite » : les rasters doivent avoir exactement la même grille ; chaque pixel prend la valeur first / last / median / mean / max / min de ses valeurs aux différentes dates (les pixels sans donnée sont ignorés) : un composite sans nuages, par exemple. « Regrouper par » fait un résultat par valeur d'attribut (une mosaïque par tuile, un composite par mois).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Mode | spatial : mosaïque d'emprises ; composite : même emprise, dates différentes. | `spatial` |
| Regrouper par | Un résultat par valeur ; vide = un seul résultat. |  |
| Trier par | Attribut qui ordonne les rasters (le dernier recouvre les autres). |  |
| Méthode (composite) | Valeur retenue pour chaque pixel. | `median` |
| Écrire en GeoTIFF | Spatial : écrit un vrai fichier au lieu d'un raster virtuel (toujours vrai pour un composite). | false |

### Raster vers polygones — `Polygonize` {#polygonize}

Transforme un raster en polygones : un polygone par ensemble de pixels voisins de même valeur.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Pour un raster de classes (occupation du sol, résultat d'un seuil ou d'un reclassement), chaque zone d'un seul tenant de même valeur devient un polygone avec sa valeur (attribut « Attribut de la valeur ») et le numéro de la bande. « Connexité » : 4 (pixels qui se touchent par un côté) ou 8 (aussi par un coin). Les pixels sans donnée ne donnent rien. « Arrondir » regroupe les valeurs décimales proches (1 = au dixième) ; « Surface minimale » écarte les polygones plus petits (dans l'unité du système au carré). Le raster entier est lu en mémoire (au plus 60 millions de pixels : découper ou réduire la résolution au-delà). Les attributs du raster sont recopiés.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bande | Bande à vectoriser (à partir de 1). | `1` |
| Attribut de la valeur | Reçoit la valeur des pixels. | `value` |
| Connexité | 4 ou 8 voisins. | `4` |
| Arrondir | Décimales conservées avant de regrouper ; vide = valeurs exactes. |  |
| Surface minimale | Ignore les polygones plus petits. | `0.0` |

### Recherche STAC — `STACSearch` {#stacsearch}

Cherche des images dans un catalogue STAC (Sentinel-2, Landsat, MNT…) et les référence sans les télécharger.

- **Entrées** : aucune — **Sorties** : `output` — **Traitement** : au fil de l'eau

Interroge l'API STAC « Adresse du catalogue » (Earth Search d'Element 84 par défaut ; Planetary Computer : https://planetarycomputer.microsoft.com/api/stac/v1 avec la signature activée). Filtres : collections, emprise (longitude/latitude WGS84), dates (2024-06-01/2024-06-30), propriétés (JSON, ex. {"eo:cloud_cover": {"lt": 20}}). Avec « Géométries reçues », chaque entité de l'entrée donne une recherche (son emprise) et ses attributs sont recopiés. « Assets » liste les bandes voulues (ex. red, nir : leurs noms dans le catalogue) : chaque scène devient un raster virtuel dont les bandes sont ces assets, lus à la demande en HTTP (rien n'est téléchargé en entier). Chaque entité porte l'identifiant de la scène, sa date, sa collection et les propriétés demandées. Le proxy du poste est utilisé.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Adresse du catalogue | Racine de l'API STAC. | `https://earth-search.aws.element84.com/v1` |
| Collections | Collections à chercher. | `['sentinel-2-l2a']` |
| Emprise (WGS84) | Ouest, sud, est, nord en degrés ; vide = pas de filtre (ou géométries reçues). |  |
| Géométries reçues | Une recherche par entité de l'entrée, sur son emprise. | false |
| Attributs à recopier | Attributs de l'entité reçue ajoutés aux scènes trouvées. |  |
| Dates | Une date ou un intervalle : 2024-06-01/2024-06-30 ; vide = toutes. |  |
| Propriétés (JSON) | Filtre sur les propriétés, ex. {"eo:cloud_cover": {"lt": 20}}. |  |
| Nombre maximal de scènes | Par recherche. | `50` |
| Tri | Champ de tri, précédé de - pour décroissant. | `-properties.datetime` |
| Assets (bandes) | Noms des assets à garder, dans l'ordre : ils deviennent les bandes du raster. |  |
| Propriétés à recopier | Propriétés de la scène ajoutées comme attributs (eo:cloud_cover devient cloud_cover). | `['eo:cloud_cover']` |
| Signature | planetary_computer : signe les adresses (jeton de lecture temporaire). | `none` |

### Reclasser un raster — `ReclassifyRaster` {#reclassifyraster}

Remplace des plages de valeurs d'un raster par des classes (pente en 4 classes, NDVI en eau/sol/végétation).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque règle donne une plage [De ; À[ et la classe correspondante ; les règles sont examinées dans l'ordre, la première qui convient l'emporte. Pour une valeur exacte, mettre la même valeur en « De » et « À » et cocher « Inclure la borne haute ». « Autres pixels » : sans donnée, conservés tels quels (keep) ou remplacés par « Valeur par défaut ». Les pixels sans donnée restent sans donnée. Le résultat est une bande « class ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bande | Bande à reclasser (à partir de 1). | `1` |
| Règles | Plages et classes, examinées dans l'ordre. |  |
| Autres pixels | nodata : sans donnée ; keep : valeur d'origine ; default : « Valeur par défaut ». | `nodata` |
| Valeur par défaut | Pour « Autres pixels » = default. | `0.0` |
| Type du résultat | Type des pixels du résultat. | `uint8` |
| Valeur sans donnée | Vide = 255 pour uint8, convention du type sinon. |  |

### Reprojection de rasters — `WarpRaster` {#warpraster}

Change le système de coordonnées d'un raster, avec choix de la résolution et du rééchantillonnage.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Le raster est recalculé dans le « Système cible » (EPSG:2154, EPSG:4326…). « Résolution » est la taille d'un pixel dans l'unité du système cible ; vide ou 0 = choisie pour garder à peu près le même nombre de pixels. Rééchantillonnage : nearest pour des classes (occupation du sol), bilinear ou cubic pour des valeurs continues (altitude, réflectance), average pour réduire la résolution. Le calcul se fait par fenêtres de 512 pixels, en parallèle : la mémoire reste bornée quelle que soit la taille du raster. Les pixels hors de l'image d'origine deviennent « sans donnée ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Système cible | Ex. EPSG:2154. |  |
| Résolution | Taille du pixel, dans l'unité du système cible ; 0 = automatique. | `0.0` |
| Rééchantillonnage | Méthode de calcul des nouveaux pixels. | `bilinear` |
| Valeur sans donnée | Vide = celle du raster, sinon la convention du type. |  |

### Rééchantillonner / aligner — `ResampleRaster` {#resampleraster}

Change la taille des pixels d'un raster, ou l'aligne sur la grille d'un autre raster (même pixels, même emprise).

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Sans « Aligner sur un raster de référence » : le raster garde son système et son emprise, avec des pixels de « Taille du pixel » (ou multipliés par « Facteur » : 2 = pixels deux fois plus grands). Avec la référence (deuxième entrée) : le raster est recalculé sur exactement la grille de la référence (système, taille et emprise), ce qui permet de combiner des images de résolutions différentes. nearest convient aux classes, bilinear / cubic aux valeurs continues, average pour réduire la résolution.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Aligner sur un raster de référence | Ajoute une entrée « reference » dont la grille est reprise. | false |
| Taille du pixel | Dans l'unité du système ; 0 = ne pas fixer. | `0.0` |
| Facteur | Multiplie la taille du pixel (2 = deux fois plus grand). | `1.0` |
| Rééchantillonnage | Méthode de calcul des nouveaux pixels. | `bilinear` |
| Valeur sans donnée | Vide = celle du raster. |  |

### Statistiques de rasters — `RasterStatistics` {#rasterstatistics}

Calcule le minimum, le maximum, la moyenne, l'écart-type et des percentiles de chaque raster, en attributs.

- **Entrées** : `input` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque raster reçoit, pour chaque bande choisie, les statistiques demandées dans des attributs `bande_statistique` (ndvi_mean…). min, max, mean, std, count (pixels avec donnée) et nodata_count sont exacts et calculés par fenêtres : la mémoire reste bornée. Les percentiles (p05 à p95) sont estimés sur une lecture réduite à 4 millions de pixels environ. Utile pour filtrer des scènes (couverture trop faible), choisir une étendue de couleurs ou contrôler un résultat.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bandes | Numéros (de 1) ou noms ; vide = toutes les bandes. |  |
| Statistiques | min, max, mean, std, count, nodata_count, p05, p25, p50, p75, p95. | `['min', 'max', 'mean']` |

### Statistiques zonales — `ZonalStatistics` {#zonalstatistics}

Calcule des statistiques d'un raster sous chaque polygone : moyenne, somme, minimum, maximum, majoritaire…

- **Entrées** : `zones`, `raster` — **Sorties** : `output` — **Traitement** : au fil de l'eau

Chaque polygone de « zones » reçoit les statistiques des pixels du raster qu'il recouvre (un pixel compte s'il a son centre dans le polygone, ou s'il est touché avec « Pixels touchés »). Les colonnes s'appellent `bande_statistique` (par exemple ndvi_mean). Statistiques : count, sum, mean, min, max, std, median, majority (valeur la plus fréquente : classes d'occupation du sol), unique (nombre de valeurs distinctes), area (surface des pixels valides, dans l'unité du système au carré). Les pixels sans donnée sont ignorés. S'il y a plusieurs rasters (dates, scènes), le résultat a une ligne par zone et par raster, avec les « Attributs du raster à ajouter » (par exemple la date). Un polygone hors du raster a un compte à 0 et des statistiques vides. Les polygones sont reprojetés dans le système du raster.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bandes | Numéros (de 1) ou noms des bandes ; vide = la première. |  |
| Statistiques | count, sum, mean, min, max, std, median, majority, unique ou area. | `['mean']` |
| Pixels touchés | Compte tout pixel que le polygone touche. | false |
| Attributs du raster à ajouter | Recopiés sur chaque ligne (la date d'une scène…). |  |
| Préfixe | Ajouté devant le nom de chaque colonne de statistique. |  |

### Valeur du raster sous des points — `SampleRasterValues` {#samplerastervalues}

Ajoute à chaque point la valeur d'un raster à cet endroit (altitude d'un point, occupation du sol d'une parcelle).

- **Entrées** : `points`, `raster` — **Sorties** : `output`, `Outside` — **Traitement** : au fil de l'eau

Chaque entité de « points » reçoit, pour chaque bande choisie, la valeur du pixel qui la contient (attribut `préfixe` + nom de la bande). Les points sont reprojetés dans le système du raster. Avec plusieurs rasters, le premier qui recouvre le point est utilisé, et `_raster` donne son numéro (de 0). Les points hors de tous les rasters, ou sur un pixel sans donnée, ont une valeur vide ; les premiers sortent sur « Outside ». Les entités qui ne sont pas des points sont lues par leur point représentatif (centre de gravité intérieur).

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Bandes | Numéros (de 1) ou noms des bandes ; vide = toutes. |  |
| Préfixe | Ajouté devant le nom de chaque bande dans les attributs. |  |

### Écriture de rasters — `RasterWriter` {#rasterwriter}

Écrit chaque raster dans un fichier GeoTIFF ou COG (cloud optimized), avec vues d'ensemble.

- **Entrées** : `input` — **Sorties** : `summary` — **Traitement** : au fil de l'eau

Le chemin est un fichier, ou un modèle avec des {attributs} (un fichier par raster : D:/sorties/{filename}), ou un dossier (le fichier prend le nom du raster d'origine). « cog » écrit un GeoTIFF optimisé pour le nuage (tuiles, vues d'ensemble, lisible par plages sur le web) ; « gtiff » un GeoTIFF tuilé ordinaire, avec vues d'ensemble si demandé. Compression sans perte (deflate ou zstd). Sur le partage de l'équipe, le fichier est écrit en local puis publié d'un bloc. Un raster déjà existant est refusé en mode « create ».

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier ou dossier | Fichier .tif, modèle avec {attributs}, ou dossier. |  |
| Format | cog : cloud optimized GeoTIFF ; gtiff : GeoTIFF tuilé. | `cog` |
| Compression | Compression sans perte. | `deflate` |
| Vues d'ensemble | Ajoute les vues d'ensemble (pyramide) : affichage rapide. | true |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace. | `overwrite` |

## Écriture

### Écriture CSV — `CsvWriter` {#csvwriter}

Écrit les entités dans un fichier CSV (la géométrie en WKT).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Encodage utf-8. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Séparateur | Séparateur de colonnes. | `,` |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

### Écriture Excel — `ExcelWriter` {#excelwriter}

Écrit les entités dans une feuille d'un classeur Excel (.xlsx).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures Excel peuvent viser des feuilles différentes du même classeur : en mode « overwrite », seule la feuille visée est remplacée. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Feuille | Nom de la feuille ; vide = « Feuil1 ». |  |
| Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

### Écriture FlatGeobuf — `FlatGeobufWriter` {#flatgeobufwriter}

Écrit les entités dans un fichier FlatGeobuf (.fgb), avec index spatial.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

### Écriture GeoJSON — `GeoJsonWriter` {#geojsonwriter}

Écrit les entités dans un fichier GeoJSON.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Pour suivre la norme, reprojeter avant en WGS 84 (EPSG:4326). Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

### Écriture GeoPackage — `GeoPackageWriter` {#geopackagewriter}

Écrit les entités dans une couche d'un GeoPackage (.gpkg).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures GeoPackage peuvent viser des couches différentes du même fichier : en mode « overwrite », seule la couche visée est remplacée. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Couche | Nom de la couche ; vide = le nom du fichier. |  |
| Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

### Écriture (Geo)Parquet — `GeoParquetWriter` {#geoparquetwriter}

Écrit les entités dans un fichier Parquet (GeoParquet s'il y a une géométrie) ou un dossier partitionné.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

« Partitionner par » écrit un sous-dossier par valeur : « Fichier » est alors un dossier. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
| Compression | zstd : compact et rapide ; none : aucune. | `zstd` |
| Partitionner par | Colonnes dont chaque valeur donne un sous-dossier ; vide = un seul fichier. |  |

### Écriture MapInfo — `MapInfoWriter` {#mapinfowriter}

Écrit les entités dans une table MapInfo (.tab) ou un fichier MIF/MID (.mif).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Le format (TAB ou MIF) est déduit de l'extension. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Encodage | Vide = celui du format. |  |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |

### Écriture Oracle — `OracleWriter` {#oraclewriter}

Écrit les entités dans une table Oracle (créée si besoin), en une seule transaction.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Modes : create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. Les lignes sont écrites dans une transaction ; mais Oracle valide aussitôt la création et la suppression d'une table (create, overwrite). La géométrie est écrite en SDO_GEOMETRY avec le SRID des données ; à la création, les métadonnées (USER_SDO_GEOM_METADATA) et l'index spatial sont créés. Les noms de table et de colonnes créés sont en majuscules.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| Table | Schéma puis table (existante, ou nouvelle à créer : taper son nom). |  |
| Mode | create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. | `append` |
| Clé (upsert / clé primaire) | Colonnes identifiant une ligne. |  |
| Colonne géométrique créée | Nom de la colonne géométrique, utilisé quand la table est créée. | `geom` |
| Index spatial | Crée un index spatial avec la table. | true |
| Système de coordonnées | Vide = celui des données. |  |

### Écriture PostgreSQL / PostGIS — `PostgresWriter` {#postgreswriter}

Écrit les entités dans une table PostgreSQL (créée si besoin), en une seule transaction.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Modes : « create » échoue si la table existe ; « overwrite » la recrée ; « append » ajoute ; « truncate » la vide puis insère ; « upsert » met à jour les lignes de même clé et insère les autres. Tout est écrit dans une transaction : en cas d'erreur, la table reste comme avant. Les colonnes des données doivent exister dans la table (hors création). Un index spatial GIST est créé avec la table. Avec « auto », polygones et lignes sont créés en « Multi » (les géométries simples sont promues) ; sinon le type du premier lot : le fixer explicitement si les données sont hétérogènes.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Connexion | Connexion nommée (menu Connexions). |  |
| Table | Schéma puis table (existante, ou nouvelle à créer : taper son nom). |  |
| Mode | create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; upsert : met à jour les lignes de même clé et insère les autres. | `append` |
| Clé (upsert / clé primaire) | Colonnes identifiant une ligne. |  |
| Colonne géométrique créée | Nom de la colonne géométrique, utilisé quand la table est créée. | `geom` |
| Type de géométrie (création) | Type de la colonne créée. auto : multi-surfaces et multi-lignes pour les polygones et les lignes, sinon le type du premier lot. | `auto` |
| Index spatial (GIST) | Crée un index spatial GIST avec la table. | true |
| Système de coordonnées | Vide = celui des données. |  |

### Écriture SQLite / SpatiaLite — `SpatiaLiteWriter` {#spatialitewriter}

Écrit les entités dans une table d'une base SpatiaLite (.sqlite), créée si besoin.

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Plusieurs écritures peuvent viser des tables différentes de la même base : en mode « overwrite », seule la table visée est remplacée. Une base créée par Carriole est une base SpatiaLite (lue par QGIS…). Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Table | Nom de la table ; vide = le nom du fichier. |  |
| Mode | create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier restent) ; append : ajoute. | `overwrite` |

### Écriture Shapefile — `ShapefileWriter` {#shapefilewriter}

Écrit les entités dans un Shapefile (.shp).

- **Entrées** : `input` — **Sorties** : aucune — **Traitement** : au fil de l'eau

Noms de colonnes limités à 10 caractères (les renommages sont journalisés), un seul type de géométrie, limite de 2 Go. Un fichier .cpg indique l'encodage. Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de route laisse le fichier existant intact.

| Paramètre | Rôle | Défaut |
| --- | --- | --- |
| Fichier | Chemin du fichier à écrire (dossiers créés si besoin). |  |
| Encodage | Vide = utf-8 (écrit dans le .cpg). |  |
| Mode | create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute. | `overwrite` |
