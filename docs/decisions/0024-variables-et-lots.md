# ADR 0024 — Variables du flux, dossiers de fichiers et traitement par lots

- **Statut** : accepté
- **Date** : 2026-10-12

## Contexte
Un flux ne sait traiter qu'un fichier à la fois, avec des paramètres saisis au lancement. Les équipes SIG ont des
dossiers de dizaines de fichiers (une commune par Shapefile), des valeurs qui dépendent des données (« la date la plus
récente de la table ») et des flux qui en appellent d'autres.

## Décisions
**Variables** (une seule syntaxe, `${…}`, dans n'importe quel paramètre de nœud) :

| Forme | Valeur | Résolue |
| --- | --- | --- |
| `${NOM}` | paramètre publié, ou variable du flux (bloc `variables`) | à l'analyse et au début du run |
| `${env:NOM}` | variable d'environnement | idem |
| `${sys:date}`, `datetime`, `timestamp`, `user`, `temp_dir`, `flow_dir`, `flow_name`, `flow_path`, `run_id` | le contexte du run | idem |
| `${global:NOM}` | réglage `variables` de l'utilisateur (`settings.json`) | idem |
| `${var:NOM}` | posée **par les données**, par un nœud `SetVariables` en amont | **au démarrage du nœud** |

- Les variables du flux se composent entre elles (`${BASE}/sortie`), dans n'importe quel ordre ; une référence circulaire
  ou un nom déjà pris par un paramètre publié est refusé.
- **Liaison tardive** de `${var:…}` : le moteur démarre un nœud quand tout son amont est terminé, donc la valeur est
  connue. À l'analyse, le paramètre est gardé tel quel (les champs texte le supportent ; un champ non texte est validé au
  démarrage du nœud). L'analyse signale en erreur une variable que **aucun SetVariables en amont** ne pose. L'empreinte
  du cache est calculée sur le texte non résolu : elle change avec l'amont, donc avec la valeur.
- `flow_dir`, `flow_name` et `flow_path` viennent du fichier du flux (`etl run flux.json`, ou le flux ouvert dans
  l'application) ; pour un flux non enregistré, `${sys:flow_dir}` est refusé avec un message clair.

**Dossiers et listes de fichiers** (catégorie « Lots et variables ») :
- `ListFiles` : une entité par fichier d'un dossier (motifs, sous-dossiers) ou d'un fichier liste ;
- `ReadListedFiles` : lit le fichier désigné par chaque entité, **union des schémas par nom** (deux types incompatibles
  deviennent du texte) ; un fichier d'exemple donne le schéma à l'analyse ;
- `SplitToFiles` : un fichier par groupe, le chemin se compose avec `{attribut}` ; sortie `summary` ;
- `Reader` accepte une liste « Autres fichiers » en plus du motif.

**Lots** : le même flux, N fois.
- `etl run flux.json --each "D:\dossier\*.shp" --as SRC [--parallel 4]`, `--each-list liste.txt`, `--each-csv params.csv`
  (une ligne = un run). Chaque run est un **sous-processus** `etl run` (mémoire isolée, un plantage n'arrête pas les
  autres) ; code de sortie non nul si un run échoue.
- `RunFlow` : même mécanique, un flux par entité reçue, paramètres tirés des attributs ; sorties `Succeeded` /
  `Failed`. Le module `etl_engine.batch` est commun à la CLI et au modificateur.
- Côté application : « En lot… » crée un run par fichier du motif (ou par valeur d'une liste), regroupés dans la liste des
  runs (`options.batch`) ; un lot est refusé en entier si le flux a des erreurs, et limité à 500 runs.

## Conséquences
- Un lot lancé depuis l'application passe par la file de runs : il respecte les quotas et le nombre de workers.
- `RunFlow` lance des processus Python (quelques secondes de démarrage chacun) : pour des milliers de tout petits
  fichiers, `ReadListedFiles` (un seul run) est plus efficace.
- Un `SetVariables` ne voit que ce qui lui arrive : pour une valeur agrégée (la date maximale), la calculer avant.
