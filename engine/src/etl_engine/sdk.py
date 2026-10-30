"""SDK des modificateurs : tout ce qu'il faut importer pour écrire un modificateur.

Un modificateur = une classe dans un fichier. Exemple minimal :

    from etl_engine.sdk import Modifier, Params, Attribute

    class Upper(Modifier):
        name = "Upper"
        label = "Majuscules"
        category = "Attributs"

        class Params(Params):
            attribute: Attribute = ""

        def process(self, batch):
            ...
            return {"output": batch}
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, get_args

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from etl_engine.data import DEFAULT_BATCH_ROWS, REJECT_COL

REJECTED = "Rejected"


class ConfigError(Exception):
    """Mauvaise configuration d'un modificateur : arrête le run (contrairement à une erreur de donnée)."""


class Cancelled(Exception):
    """Run annulé (demande utilisateur ou délai maximal)."""


class Params(BaseModel):
    """Base des paramètres d'un modificateur : pas de champ inconnu."""

    model_config = ConfigDict(extra="forbid")


def _widget(kind: str, **extra: Any) -> Any:
    return Field(json_schema_extra={"x-etl-widget": kind, **extra})


# Types de paramètres métier : l'éditeur en fait le bon champ de formulaire.
Attribute = Annotated[str, _widget("attribute")]  # un attribut présent en amont
AttributeList = Annotated[list[str], _widget("attributes")]  # plusieurs attributs


def attribute_of(port: str) -> Any:
    """Attribut d'une entrée précise d'un modificateur à plusieurs entrées : l'éditeur ne propose que les siens."""
    return Annotated[str, _widget("attribute", **{"x-etl-input": port})]


def attributes_of(port: str) -> Any:
    """Plusieurs attributs d'une entrée précise (cases à cocher de ses seuls attributs, « Tout » / « Aucun »)."""
    return Annotated[list[str], _widget("attributes", **{"x-etl-input": port})]


Expression = Annotated[str, _widget("expression")]  # expression SQL DuckDB
Crs = Annotated[str, _widget("crs")]  # système de coordonnées (ex. EPSG:2154)
FilePath = Annotated[str, _widget("path")]  # fichier ou dossier à lire (explorateur sur les dossiers de lecture)
OutPath = Annotated[str, _widget("path", **{"x-etl-access": "write"})]  # fichier ou dossier à écrire
DbConnection = Annotated[str, _widget("connection")]  # connexion de base nommée (tous types)


def db_connection(*kinds: str) -> Any:
    """Connexion nommée d'un type de base (`postgres`, `oracle`) : la liste n'offre que celles-là."""
    return _widget("connection", **{"x-etl-kinds": list(kinds)})


# « schema.table » d'une base : choisis dans des listes (schémas, puis tables) lues sur la connexion du paramètre
# `connection` du même modificateur ; PgTableOut accepte aussi une table qui n'existe pas encore (écriture)
PgTable = Annotated[str, _widget("pg-table", **{"x-etl-connection": "connection"})]
PgTableOut = Annotated[str, _widget("pg-table", **{"x-etl-connection": "connection", "x-etl-access": "write"})]
PythonCode = Annotated[str, _widget("code", **{"x-etl-language": "python"})]  # éditeur de code Python
SqlCode = Annotated[str, _widget("code", **{"x-etl-language": "sql"})]  # éditeur de code SQL

Emit = Callable[[str, pa.Table], None]


@dataclass
class Context:
    """Ce que le moteur donne à un modificateur : journal, progression, annulation, paramètres publiés."""

    node_id: str = ""
    params: Mapping[str, str] = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)
    batch_rows: int = DEFAULT_BATCH_ROWS
    workers: int = 1  # lots traités en parallèle par un modificateur « streaming » sans état
    connections: Any = None
    log_sink: Callable[[str, str, str], None] | None = None  # (nœud, niveau, message)
    progress_sink: Callable[[str, int], None] | None = None  # (nœud, lignes lues)
    variables: dict[str, str] = field(default_factory=dict)  # `${var:NOM}` : partagé par tous les nœuds du run
    work_dir: Path | None = None  # dossier du nœud pour ses fichiers produits (rasters), supprimé avec son cache

    def set_variable(self, name: str, value: str) -> None:
        """Pose une variable lue par les nœuds en aval (`${var:NOM}`)."""
        self.variables[name] = value

    def log(self, level: str, message: str) -> None:
        if self.log_sink:
            self.log_sink(self.node_id, level, message)
        else:
            logging.getLogger("etl").log(getattr(logging, level), "[%s] %s", self.node_id, message)

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def warning(self, message: str) -> None:
        self.log("WARNING", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled

    def progress(self, rows: int) -> None:
        if self.progress_sink:
            self.progress_sink(self.node_id, rows)


def missing_attributes(names: Iterable[str]) -> ConfigError:
    """Erreur de configuration « attribut(s) absent(s) » : les noms séparés par des virgules, la même forme pour tous
    les modificateurs (à lever : `raise missing_attributes(absents)`)."""
    names = list(names)
    listing = ", ".join(names)
    if len(names) == 1:
        return ConfigError(f"attribut absent : {listing}")
    return ConfigError(f"attributs absents : {listing}")


def require_attributes(schema: pa.Schema | None, names: Iterable[str]) -> None:
    """Dans `output_schema` : lève `missing_attributes` si l'un des attributs manque au schéma de l'amont (rien si le
    schéma n'est pas connu) ; le run s'arrête alors avant de lire les données."""
    if schema is not None and (missing := [n for n in names if n and n not in schema.names]):
        raise missing_attributes(missing)


def reject(table: pa.Table, reason: str | pa.Array) -> pa.Table:
    """Ajoute `_rejection_reason` à `table` : à renvoyer sur le port `Rejected`."""
    values = pa.repeat(pa.scalar(reason, pa.string()), table.num_rows) if isinstance(reason, str) else reason
    if REJECT_COL in table.column_names:
        table = table.drop_columns([REJECT_COL])
    return table.append_column(REJECT_COL, values)


class Modifier:
    """Classe de base. Voir `docs/ecrire_un_modificateur.md`."""

    __test__ = False  # un modificateur nommé « Test… » ne doit pas être pris pour une classe de test pytest

    # --- déclaration -----------------------------------------------------------------
    name: ClassVar[str] = ""  # identifiant stable, unique (ex. "AddAttributes")
    label: ClassVar[str] = ""  # libellé affiché (français)
    category: ClassVar[str] = "Divers"
    description: ClassVar[str] = ""
    help: ClassVar[str] = ""
    version: ClassVar[int] = 1
    mode: ClassVar[Literal["streaming", "blocking"]] = "streaming"
    inputs: ClassVar[tuple[str, ...]] = ("input",)
    outputs: ClassVar[tuple[str, ...]] = ("output",)
    rejected: ClassVar[bool] = True  # ajoute le port `Rejected`
    stateful: ClassVar[bool] = False  # vrai : les lots doivent être traités dans l'ordre, un par un
    hidden: ClassVar[bool] = False  # vrai : hors de la palette et du catalogue (ancien modificateur, encore exécuté)
    # faux : toujours relancé, jamais repris d'un run précédent (effet hors du flux : écriture, flux lancé, SQL qui
    # modifie la base). « partial » : source qui change sans que le flux change (base, API…) : relue à chaque run,
    # mais reprise dans un run « jusqu'au nœud ». Un modificateur sans sortie (écriture) n'est jamais repris.
    reusable: ClassVar[bool | Literal["partial"]] = True
    # calcul géométrique : lots coupés en tranches de cette taille (mémoire bornée par tâche, cœurs mieux occupés)
    slice_rows: ClassVar[int | None] = None
    # voie SQL d'une lecture : si DuckDB échoue (type qu'il ne sait pas écrire…), le moteur repasse par `run` au lieu
    # de mettre le nœud en erreur
    sql_fallback: ClassVar[bool] = False

    class Params(Params):
        pass

    def __init__(self, params: BaseModel, ctx: Context) -> None:
        self.params = params
        self.ctx = ctx

    # --- ports -----------------------------------------------------------------------
    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(entrées, sorties) ; surchargeable quand les ports dépendent des paramètres."""
        outs = cls.outputs + ((REJECTED,) if cls.rejected else ())
        return cls.inputs, outs

    # --- schéma ----------------------------------------------------------------------
    def output_schema(self, inputs: dict[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        """Schéma de chaque sortie, calculé sans exécuter. Par défaut : la première entrée, inchangée."""
        first = inputs.get(self.inputs[0]) if self.inputs else None
        return {p: first for p in self.ports(self.params)[1] if p != REJECTED}

    # --- exécution -------------------------------------------------------------------
    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        """Traite un lot (mode `streaming`). Renvoie les lots par port de sortie."""
        raise NotImplementedError

    def run(self, inputs: Mapping[str, Iterable[pa.Table]], emit: Emit) -> None:
        """Pilote le modificateur. Par défaut : `process` sur chaque lot de la première entrée.

        Un modificateur bloquant ou à plusieurs entrées surcharge `run` et appelle `emit(port, table)`.
        """
        main = self.inputs[0]
        workers = 1 if self.stateful or self.mode != "streaming" else max(1, self.ctx.workers)

        def deliver(result: dict[str, pa.Table]) -> None:
            for port, table in result.items():
                if table is not None and table.num_rows:
                    emit(port, table)

        batches = inputs.get(main, ())
        if self.slice_rows:
            batches = slices(batches, self.slice_rows)
        for result in ordered_map(self.process, batches, workers, self.ctx):
            deliver(result)

    def sql(self, relations: Mapping[str, str], schemas: Mapping[str, pa.Schema | None]) -> dict[str, str] | None:
        """Voie SQL (facultative) : exécution par DuckDB, directement sur les ports.

        `relations` : port d'entrée -> nom d'une relation DuckDB ; `schemas` : leurs schémas. Renvoie, pour chaque
        port de sortie, un SELECT qui produit exactement les mêmes lignes et colonnes que `run` (un port absent : vide).
        Renvoyer `None` pour refuser (paramètres non exprimables en SQL) : le moteur utilise alors `run`.

        Un modificateur ligne à ligne (`mode = "streaming"`) qui a des entrées n'écrit pas ses sorties : elles sont
        définies par ces requêtes et calculées quand on les lit (ADR 0044). Ses expressions d'utilisateur y sont
        marquées par `expr.guarded` ; ses colonnes calculées sont déclarées par `sql_checked`.
        """
        return None

    def sql_prepare(self, con: Any) -> None:
        """Prépare la connexion DuckDB de la voie SQL (extension spatiale, lecture sur le web…) avant les requêtes."""

    def sql_checked(self) -> list[str]:
        """Voie différée (ADR 0044) : colonnes de sortie calculées par des expressions d'utilisateur. Le moteur les
        évalue sur toutes les lignes au contrôle (`count(colonne)`) : une erreur de donnée renvoie le nœud vers la
        voie par lots. Les conditions d'un filtre (`WHERE`) le sont déjà par le compte des lignes."""
        return []

    def ready_ports(self) -> dict[str, str] | None:
        """Ports dont les données sont déjà un fichier Parquet au format des ports (géométrie WKB `geometry`, SCR lu
        par `get_crs`) : le port désigne ce fichier au lieu d'en recopier les lignes (ADR 0043). Tous les ports de
        sortie, ou None : le moteur exécute alors le modificateur."""
        return None

    def fingerprint_extra(self) -> str:
        """Ajoute à l'empreinte de cache ce qui n'est pas dans les paramètres (ex. date d'un fichier lu)."""
        return ""

    def used_attributes(self) -> list[str]:
        """Attributs de la première entrée référencés par les paramètres (avertissements « absent en amont »)."""
        return used_attributes(self.params, self.inputs[0] if self.inputs else None)

    # --- description pour l'API et l'éditeur ----------------------------------------------
    @classmethod
    def describe(cls, lang: str | None = None) -> dict[str, Any]:
        """Description pour l'éditeur ; `lang` : langue des textes (le code est en français, le catalogue traduit)."""
        from etl_engine.i18n import localize_description

        return localize_description(cls.name, cls._describe_source(), lang)

    @classmethod
    def _describe_source(cls) -> dict[str, Any]:
        inputs, outputs = cls.ports(None)
        return {
            "name": cls.name,
            "label": cls.label or cls.name,
            "category": cls.category,
            "category_id": cls.category,  # clé stable de la catégorie (le français du code) : couleurs, regroupements
            "description": cls.description,
            "help": cls.help,
            "version": cls.version,
            "mode": cls.mode,
            "inputs": list(inputs),
            "outputs": list(outputs),
            "params_schema": cls.Params.model_json_schema(),
            "hidden": cls.hidden,
        }


Transformer = Modifier  # ancien nom (avant le 2026-10-20) : les plugins qui l'importent marchent toujours


def used_attributes(model: BaseModel, port: str | None = None) -> list[str]:
    """Attributs référencés par les paramètres (champs `Attribute`/`AttributeList`, y compris imbriqués) ; `port` :
    ceux de cette entrée seulement (un champ qui en désigne une autre, `attribute_of`, est laissé)."""
    found: list[str] = []

    def walk(value: Any, extra: dict[str, Any] | None) -> None:
        kind = (extra or {}).get("x-etl-widget")
        elsewhere = (extra or {}).get("x-etl-input")
        if port is not None and elsewhere is not None and elsewhere != port:
            return
        if isinstance(value, BaseModel):
            for name, info in type(value).model_fields.items():
                walk(getattr(value, name), info.json_schema_extra if isinstance(info.json_schema_extra, dict) else None)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item, {**(extra or {}), "x-etl-widget": "attribute"} if kind == "attributes" else extra)
        elif isinstance(value, str) and kind == "attribute" and value:
            found.append(value)

    walk(model, None)
    return found


def expressions(model: BaseModel) -> list[str]:
    """Expressions SQL des paramètres (champs `Expression`, y compris imbriqués)."""
    found: list[str] = []

    def widget(annotation: Any, extra: Any) -> str | None:
        if isinstance(extra, dict) and extra.get("x-etl-widget"):
            return extra["x-etl-widget"]
        for meta in getattr(annotation, "__metadata__", ()):  # Annotated[str, Field(json_schema_extra=…)]
            if isinstance(getattr(meta, "json_schema_extra", None), dict):
                return meta.json_schema_extra.get("x-etl-widget")
        return None

    def walk(value: Any, annotation: Any, extra: Any) -> None:
        if isinstance(value, BaseModel):
            for name, info in type(value).model_fields.items():
                walk(getattr(value, name), info.annotation, info.json_schema_extra)
        elif isinstance(value, list | tuple):
            args = get_args(annotation)
            for item in value:
                walk(item, args[0] if args else None, None)
        elif isinstance(value, str) and widget(annotation, extra) == "expression" and value.strip():
            found.append(value)

    walk(model, None, None)
    return found


def iter_batches(source: Iterable[pa.Table] | None) -> Iterator[pa.Table]:
    return iter(source or ())


def row_limit() -> Any:
    """Paramètre commun des lecteurs (catégorie « Lecture ») : lire seulement les premières entités. L'éditeur le
    signale sur le nœud (`x-etl-row-limit`) ; la lecture passe par `first_rows`."""
    return Field(
        0,
        ge=0,
        title="Lignes lues au plus",
        description="Pour essayer un flux sur un extrait : seules les N premières entités sont lues ; 0 = toutes.",
        examples=[1000],
        json_schema_extra={"x-etl-row-limit": True},
    )


def first_rows(batches: Iterable[pa.Table], limit: int) -> Iterator[pa.Table]:
    """Les lots de `batches` jusqu'à `limit` lignes en tout (0 : tous) ; la lecture s'arrête là."""
    if limit <= 0:
        yield from batches
        return
    left, source = limit, iter(batches)
    try:
        for batch in source:
            if batch.num_rows >= left:
                yield batch.slice(0, left)
                return
            left -= batch.num_rows
            yield batch
    finally:
        getattr(source, "close", lambda: None)()  # fichier ou curseur de base fermé sans tout lire


GEOMETRY_SLICE = 20_000  # lignes par tâche pour les calculs géométriques (voir `Modifier.slice_rows`)


def slices(batches: Iterable[pa.Table], rows: int) -> Iterator[pa.Table]:
    """Les lots coupés en tranches d'au plus `rows` lignes (sans copie) : pour les calculs géométriques, une tâche
    garde peu d'objets en mémoire et le travail se répartit mieux entre les cœurs."""
    for batch in batches:
        if batch.num_rows <= rows:
            yield batch
            continue
        for start in range(0, batch.num_rows, rows):
            yield batch.slice(start, rows)


def ordered_map(
    fn: Callable[[Any], Any], batches: Iterable[Any], workers: int, ctx: Context | None = None
) -> Iterator[Any]:
    """`fn` sur chaque lot (ou paquet), `workers` à la fois ; résultats rendus dans l'ordre (sortie déterministe).

    La fenêtre d'attente est bornée (2 × `workers` lots) : la mémoire aussi. `fn` ne doit pas modifier d'état partagé.
    """
    check = ctx.check_cancel if ctx is not None else (lambda: None)
    if workers <= 1:
        for batch in batches:
            check()
            yield fn(batch)
        return
    pending: deque[Future] = deque()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="etl-batch") as pool:
        try:
            for batch in batches:
                check()
                pending.append(pool.submit(fn, batch))
                while len(pending) >= 2 * workers or (pending and pending[0].done()):
                    yield pending.popleft().result()
            while pending:
                check()
                yield pending.popleft().result()
        finally:
            for future in pending:
                future.cancel()
