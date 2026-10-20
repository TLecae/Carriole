# ADR 0025 — Ports qui dépendent des paramètres, éditeur de code, code en ligne

- **Statut** : accepté
- **Date** : 2026-10-12

## Contexte
Plusieurs modificateurs utiles ont des ports qui dépendent de leur réglage : un filtre à N sorties nommées (`RouteByExpression`),
une requête SQL sur N entrées (`ExecuteSql`), une grille qui a ou non une entrée (`CreateGrid`). Deux autres
(`ExecuteSql`, `PythonScript`) demandent d'écrire du code dans le formulaire.

## Décisions
- **Ports dynamiques** : `Modifier.ports(params)` (classmethod, déjà utilisé par le plan d'exécution) est la source de
  vérité. `describe()` donne les ports « par défaut » (`params=None`) ; `POST /api/analyze` renvoie en plus
  `ports: {nœud: {inputs, outputs}}` calculés avec les **vrais** paramètres, et l'éditeur dessine ces ports-là. Une
  connexion qui pointe vers un port disparu devient un avertissement `bad_port` de l'analyse (rien n'est supprimé en
  silence). Un port est un identifiant (`[A-Za-z][A-Za-z0-9_]*`).
- **Valeurs par défaut des listes** : un champ liste dont le défaut n'est pas vide déclare `json_schema_extra={"default": …}`
  (Pydantic n'écrit pas le défaut d'un `default_factory` dans le schéma) : le formulaire montre la même chose que le moteur.
- **Éditeur de code** : widget `code` (`PythonCode`, `SqlCode` dans le SDK), CodeMirror 6 chargé à la demande.
- **SQL de l'utilisateur** (`ExecuteSql`) : une seule instruction `SELECT`, vérifiée par l'analyseur de DuckDB ;
  connexion sans accès aux fichiers ni au réseau ; géométrie exposée en `GEOMETRY` et reconvertie en WKB au retour.
- **Python de l'utilisateur** (`PythonScript`) : **n'est jamais exécuté à l'analyse** (compilation seulement : syntaxe et
  présence de `process`). À l'exécution il tourne avec les droits de l'utilisateur, comme un plugin : le risque est celui
  d'ouvrir un flux inconnu ([securite.md](../securite.md)), pas un risque d'isolation. Son schéma de sortie est inconnu.
- **`HttpRequest`** : http(s) seulement, proxy du poste, réponse plafonnée à 5 Mo par appel, reprises limitées, débit
  réglable. Les jetons se passent par `${env:NOM}` (jamais dans le flux).

## Conséquences
- Les formulaires des nœuds en aval d'un `PythonScript` ou d'un `PivotWider` sans colonnes déclarées ne proposent
  pas d'attributs (schéma inconnu) ; c'est signalé dans l'aide de chacun.
- Renommer une sortie de `RouteByExpression` laisse les liens existants en erreur jusqu'à ce qu'ils soient refaits.
