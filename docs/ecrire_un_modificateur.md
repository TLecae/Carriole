# Écrire un modificateur

Un modificateur est **une classe Python dans un seul fichier**. Déposez le fichier dans le
dossier de plugins : il apparaît dans la palette de l'éditeur, sans reconstruire quoi que ce soit.

## En 2 minutes

```bash
# crée plugins/surface_calculator.py et plugins/test_surface_calculator.py
etl new-modifier SurfaceCalculator --dir plugins --category Géométrie
# les plugins de l'utilisateur vont dans %LOCALAPPDATA%\Carriole\plugins (ou le dossier ETL_PLUGINS_DIR)
```

Un exemple complet et commenté : [examples/modifiers/surface_calculator.py](../examples/modifiers/surface_calculator.py).

## Anatomie

```python
import pyarrow as pa
from etl_engine.sdk import Attribute, Modifier, Params, reject


class MonModifier(Modifier):
    name = "MonModifier"  # identifiant stable (utilisé dans les flux enregistrés)
    label = "Mon modificateur"  # libellé de la palette
    category = "Attributs"  # dossier de la palette
    description = "Une ligne."
    help = "Aide affichée dans l'éditeur."

    class Params(Params):  # → formulaire généré automatiquement
        attribute: Attribute = ""  # liste des attributs disponibles en amont
        seuil: int = 10

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        # `batch` : un lot d'entités (table Arrow), géométrie en WKB dans la colonne "geometry"
        return {"output": batch}  # un lot par port de sortie
```

### Ports

`inputs = ("input",)` et `outputs = ("output",)` par défaut ; un port **`Rejected`** est ajouté
automatiquement. `ExtractByExpression` déclare `outputs = ("Passed", "Failed")`. Un modificateur sans entrée
(lecture) met `inputs = ()` ; sans sortie (écriture) `outputs = ()` et `rejected = False`.

### Paramètres

Les paramètres sont une classe Pydantic. Le type du champ décide du widget :

| Type | Widget |
| --- | --- |
| `str`, `int`, `float`, `bool` | texte, nombre, case à cocher |
| `Literal["a", "b"]` | liste de choix |
| `list[Literal["a", "b"]]` | cases à cocher (plusieurs choix) |
| `Attribute` / `AttributeList` | un attribut présent en amont / cases à cocher (ordre des coches gardé, filtre au-delà de 8) |
| `Expression` | expression SQL DuckDB (éditeur avec complétion des attributs) |
| `Crs` | système de coordonnées (ex. `EPSG:2154`) |
| `FilePath` | chemin de fichier ou de dossier |
| `DbConnection` | connexion de base nommée |
| `list[AutreClasseParams]` | tableau de lignes |

Au-delà de 6 paramètres, le panneau propose une recherche (libellé, nom, description, valeurs proposées).

Dans n'importe quel champ texte on peut écrire `${NOM}` (paramètre publié du flux) ou
`${env:NOM}` (variable d'environnement) : le moteur les remplace avant de valider.

### Lots et entités

Le moteur découpe les données en lots (100 000 lignes par défaut) : `process` reçoit un lot et
renvoie un lot par port. Les lots sont indépendants : n'y gardez pas d'état entre deux appels
(sinon `stateful = True`). Pour les données :

- `etl_engine.data.geometries(batch)` → tableau Shapely ; `set_geometries(batch, geoms)` pour l'inverse ;
- `etl_engine.expr.evaluate(batch, {"x": "SQL…"})` évalue des expressions SQL DuckDB ;
- `etl_engine.data.get_crs(batch)` / `with_crs(batch, "EPSG:…")` pour le système de coordonnées.

### Géométries : les règles qui font la vitesse

Les lots d'un modificateur « au fil de l'eau » sont traités sur plusieurs cœurs à la fois. Ce qui s'exécute en
Python (une boucle par entité) bloque les autres threads : un calcul de surface écrit ainsi était 5 fois plus lent
que sa version vectorisée. Donc :

- **jamais de boucle par entité** : `data.type_ids(geoms)` (avec `POINTS`, `LINES`, `AREAS`), `data.present(geoms)`,
  et les fonctions Shapely sur tout le tableau (`shapely.get_parts(…, return_index=True)`,
  `shapely.get_coordinates(…, return_index=True)`, `shapely.multipolygons(parts, indices=…)`…) ;
- **ne réencodez que ce qui change** : `data.replace_geometries(batch, geoms, changed)` garde le WKB d'origine des
  lignes non modifiées ;
- `slice_rows = GEOMETRY_SLICE` (de `etl_engine.sdk`) : les lots arrivent en tranches de 20 000 lignes (mémoire
  bornée par tâche, cœurs mieux occupés) ; un `run` à soi passe par `ordered_map(fn, slices(lots, GEOMETRY_SLICE), …)` ;
- ce qui ne change pas d'un lot à l'autre (lecture d'un SCR, transformation pyproj) est mis en cache
  (`data.is_geographic(crs)`) ;
- relations entre deux entrées : `etl_engine.spatial.SpatialIndex` (la grande géométrie préparée, découpe locale,
  erreurs GEOS isolées ; voir l'ADR 0029) ; fusion de nombreuses géométries : `shapely.disjoint_subset_union_all`.

### Erreurs

- Une **entité** en erreur part sur `Rejected` : `reject(lignes, "motif")` ajoute `_rejection_reason`.
  Le run continue, l'inspecteur montre les rejets et leur motif.
- Une **erreur de configuration** (paramètre absurde, attribut absent) : `raise ConfigError("message en français")`.
  Le run s'arrête avant de lire la moindre donnée quand l'erreur est détectable par `output_schema`.

### Schéma de sortie (sans exécuter)

Surchargez `output_schema(self, inputs)` pour déclarer les colonnes produites : l'éditeur propose
les attributs en aval **avant le premier run**. Sans surcharge, la sortie a le schéma de l'entrée.

### Modificateurs bloquants et multi-entrées

Un tri, une agrégation ou une jointure a besoin de toutes les données : `mode = "blocking"` et on
surcharge `run(self, inputs, emit)` — `inputs["port"]` est un itérable de lots, `emit("port", table)`
envoie un lot en sortie.

```python
def run(self, inputs, emit):
    tout = pa.concat_tables(inputs["input"])  # tout en mémoire : réservé aux petits volumes
    emit("output", tout.sort_by("pop"))
```

### En SQL : exécuté par DuckDB (facultatif)

Un modificateur peut aussi s'écrire en SQL : `sql(self, relations, schemas)` renvoie, pour chaque port de sortie, un
`SELECT` sur les relations d'entrée (`relations["input"]`) qui donne **exactement** les mêmes lignes et colonnes que la
voie par lots ; `None` pour refuser (réglages non exprimables). Le moteur choisit alors :

- modificateur ligne à ligne (`mode = "streaming"`) : ses ports ne sont **pas écrits**. Leur requête est contrôlée sur
  toutes les lignes et comptée, puis gardée comme définition ; une chaîne de tels nœuds forme une seule requête,
  calculée par le nœud qui en lit les données ou par l'inspecteur à l'ouverture
  ([ADR 0044](decisions/0044-ports-calcules-a-la-demande.md)) ;
- modificateur bloquant : la requête est exécutée et ses ports écrits, DuckDB débordant sur disque au besoin.

Les expressions d'utilisateur se marquent par `expr.guarded(…)` et les colonnes qu'elles calculent se déclarent par
`sql_checked()` : une erreur sur une donnée renvoie alors le nœud vers la voie par lots, qui rejette exactement les
entités en cause. `expr.sql_base` et `expr.sql_columns` donnent aux expressions la même vue des lignes que la voie par
lots (géométrie en `GEOMETRY`, WKB d'origine gardé). Exemple : `ExtractByExpression`.

## Tester

```python
from etl_engine.testing import run_modifier, table


def test_mon_modifier():
    sortie = run_modifier(MonModifier, {"attribute": "a"}, table(a=[1, 2, 3]))
    assert sortie["output"].num_rows == 3
```

`run_modifier` découpe l'entrée en petits lots pour vérifier que le traitement par lots est correct.
`points([x…], [y…], nom=[…])` fabrique une table de points. Lancez : `pytest plugins/`.

## Déployer

- **Dossier de plugins** : copier le `.py` dans `PLUGINS_DIR` (monté dans `/plugins`), redémarrer l'API et les workers.
  Un plugin cassé est ignoré et signalé (`etl modifiers list`), il n'empêche pas les autres.
- **Paquet Python** : déclarer un entry point `etl.modifiers` dans le `pyproject.toml` du paquet.

## Ports qui dépendent des paramètres, champs de code

Surcharger `ports(cls, params)` quand les entrées ou sorties dépendent du réglage (`params` vaut `None` pour la palette) ;
l'analyse renvoie les ports réels de chaque nœud à l'éditeur. Les types `PythonCode` et `SqlCode` (SDK) affichent un
éditeur de code. Une liste dont le défaut n'est pas vide doit l'annoncer au formulaire :
`Field(default_factory=lambda: ["input"], json_schema_extra={"default": ["input"]})`
([ADR 0025](decisions/0025-ports-dynamiques-et-code-en-ligne.md)).

## Traduire un modificateur

Les textes du code restent en français. Pour l'anglais (ou une autre langue), un fichier `<plugin>.locales.json` à côté du
`.py` donne la traduction ; une clé absente retombe sur le français ([ADR 0023](decisions/0023-internationalisation.md)) :

```json
{
  "en": {
    "modifiers": {
      "MonModifier.label": "My modifier",
      "MonModifier.description": "Does something.",
      "MonModifier.params.attribute.title": "Attribute"
    },
    "messages": { "attribut absent : {attribute}": "missing attribute: {attribute}" }
  }
}
```

Les clés des paramètres sont `<Nom>.params.<champ>.title` / `.description` (`<Nom>.defs.<Modèle>.<champ>…` pour un modèle
imbriqué). Les messages sont le texte français, avec les mêmes `{valeurs}` que dans le `raise`.
