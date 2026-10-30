"""Service de données de l'inspecteur : tables filtrées/triées et tuiles vectorielles, calculées par DuckDB sur le cache.

L'inspecteur travaille sur toutes les entités d'un port. La table (pages chargées au fil du défilement, filtres, tri,
statistiques, export) lit le port. La carte lit une vue préparée en arrière-plan à la première ouverture du port
(`etl_engine.view` : morceaux reprojetés et généralisés par zoom), jamais pendant le run : la table est consultable
pendant ce temps ; ses tuiles des petites échelles sont ensuite calculées d'avance. Le navigateur ne reçoit jamais le
jeu complet : pages de lignes, statistiques, tuiles MVT (au plus `MAP_LIMIT` entités par métatuile : les plus
grandes), géométrie de la sélection généralisée si elle est énorme.

Sécurité : la connexion DuckDB est verrouillée sur le dossier du cache (`allowed_directories`) sans accès réseau,
ne peut ni écrire ni changer ses réglages ; les identifiants de run/nœud/port sont validés ; la clause WHERE libre
doit être une expression unique ; chaque requête a un délai maximal.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from etl_engine import duck
from etl_engine import view as views
from etl_engine.cache import SourceChanged, has_port, materialize, port_data, sql_path
from etl_engine.data import GEOM, get_crs
from etl_engine.expr import check_single_expression
from etl_engine.sdk import ConfigError
from pydantic import BaseModel, Field

SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
QUERY_TIMEOUT = 30.0
TABLE_TIMEOUT = 120.0  # page de la table, ligne d'une entité cliquée : attendues avec un indicateur, triées en entier
TILE_CAP = 20_000  # morceaux de géométrie maximum par tuile (échantillonnage au-delà)
MAP_LIMIT = 50_000  # entités dessinées au plus par métatuile : au-delà, les plus grandes et « zoomez »
META = 2  # une métatuile : 4 × 4 tuiles du même zoom, qui partagent le choix de leurs entités
PREBUILD_MAX_ZOOM = 10  # tuiles calculées d'avance (sans filtre) jusqu'à ce zoom…
PREBUILD_MAX_TILES = 2_000  # … et au plus autant de tuiles par port (un jeu mondial s'arrête plus tôt)
MAX_EVERY = 2**20
EXTENT, TILE_BUFFER = 4096, 64  # unités d'une tuile MVT, marge autour
PIXEL = 8  # unités par pixel (tuile de 512 px)
HEAVY_POLYGON = 256  # sommets au-delà desquels un polygone de la tuile est simplifié anneau par anneau
FEATURE_MAX_POINTS = 100_000  # au-delà, la géométrie de la sélection est généralisée
FEATURE_PIXELS = 1_000  # finesse de la généralisation : l'entité tient sur ~1 000 pixels (la carte, zoomée dessus)
WORLD = 20037508.342789244  # demi-largeur du monde en EPSG:3857
WGS84 = "EPSG:4326"
EXACT_DISTINCT_MAX_ROWS = 2_000_000
NEEDS_GEOMETRY = re.compile(r"\bST_|\bgeometry\b", re.IGNORECASE)


OUT_OF_MEMORY = 507  # statut d'une requête qui dépasse la mémoire de l'inspecteur
PREPARING = 425  # port calculé à la demande (ADR 0044) : ses données se préparent, redemander un peu plus tard
ABANDONED_S = 5.0  # préparation que l'interface ne redemande plus depuis ce délai (autre port affiché) : arrêtée

# ports calculés à la demande : écrits en arrière-plan à leur première ouverture, une fois
_DATA_BUILDS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="etl-data")
_data_jobs: dict[Path, Future] = {}
_data_asked: dict[Path, float] = {}  # dernière demande des données de chaque port en préparation
_data_guard = threading.Lock()

# vues cartographiques préparées en arrière-plan : la table n'attend pas la carte
_VIEW_BUILDS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="etl-view")
_builds: dict[Path, Future] = {}
_builds_guard = threading.Lock()
# tuiles des petites échelles calculées d'avance, une fois la vue prête (à côté des tuiles demandées par la carte)
_PREBUILDS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="etl-tiles")
_prebuilt: set[tuple[Path, int]] = set()  # vues dont les tuiles d'avance sont faites
_prebuilding: set[tuple[Path, int]] = set()  # … en cours
_ahead_of: tuple[Path, int] | None = None  # la seule vue dont on calcule les tuiles d'avance : le dernier port ouvert
# choix des entités d'une métatuile : en mémoire (et sur disque, à côté des tuiles)
_selections: OrderedDict[str, tuple[int | None, int]] = OrderedDict()
_selection_locks: dict[str, threading.Lock] = {}
_selections_guard = threading.Lock()


class DataError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class Abandoned(DataError):
    """Tuile que plus personne n'attend (la carte montre un autre port, ou est fermée) : rien n'est calculé."""

    def __init__(self) -> None:
        super().__init__("tuile abandonnée", 499)


class _Gate:
    """Places de calcul des tuiles, en nombre limité : une carte qui en demande vingt ne prend pas toute la mémoire ni
    tous les cœurs. Les tuiles demandées par la carte passent avant celles calculées d'avance ; une tuile abandonnée
    quitte la file sans rien calculer : le port qu'on regarde n'attend pas les tuiles du précédent."""

    def __init__(self, slots: int) -> None:
        self._free = slots
        self._asked = 0  # tuiles de la carte en attente d'une place
        self._cond = threading.Condition()

    @contextmanager
    def slot(self, abandoned: Callable[[], bool], ahead: bool = False) -> Iterator[None]:
        with self._cond:
            self._asked += not ahead
            try:
                while True:
                    if abandoned():
                        raise Abandoned
                    if self._free and not (ahead and self._asked):
                        break
                    self._cond.wait(0.1)  # un abandon n'est pas notifié : on le guette
            finally:
                self._asked -= not ahead
            self._free -= 1
        try:
            yield
        finally:
            with self._cond:
                self._free += 1
                self._cond.notify_all()


_GATE = _Gate(max(2, (os.cpu_count() or 2) // 2))


# ------------------------------------------------------------------------------------------ requêtes

Op = Literal[
    "contains",
    "not_contains",
    "starts",
    "ends",
    "equals",
    "not_equals",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "in",
    "null",
    "not_null",
]


class ColumnFilter(BaseModel):
    column: str
    op: Op
    value: Any = None
    value2: Any = None
    values: list[Any] = []


class SortKey(BaseModel):
    column: str
    desc: bool = False


class Query(BaseModel):
    filters: list[ColumnFilter] = []
    where: str = ""  # expression SQL libre
    search: str = ""  # recherche globale
    sort: list[SortKey] = []
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=1000)

    def filter_key(self) -> dict[str, Any]:
        """Ce qui détermine l'ensemble d'entités (pas la pagination ni le tri) : sert de clé de cache des tuiles."""
        return self.model_dump(exclude={"offset", "limit", "sort"})


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


KINDS = "POINT", "LINESTRING", "POLYGON", "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION"


def _wkb_kind(column: str) -> str:
    """Type d'une géométrie WKB (« POLYGON »…) lu dans son en-tête : sans la décoder (une géométrie fusionnée peut
    peser des centaines de Mo, la décoder coûte plusieurs fois sa taille en mémoire)."""

    def byte(i: int) -> str:
        return f"('0x' || hex({column}[{i}:{i}]))::INTEGER"

    code = f"(CASE WHEN {byte(1)} = 0 THEN {byte(5)} + 256 * {byte(4)} ELSE {byte(2)} + 256 * {byte(3)} END) % 1000"
    whens = " ".join(f"WHEN {i} THEN '{name}'" for i, name in enumerate(KINDS, 1))
    return f"(CASE {code} {whens} END)"


def category(duck_type: str) -> str:
    t = duck_type.upper()
    if t.startswith("DECIMAL") or t in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "FLOAT",
        "DOUBLE",
    }:
        return "numeric"
    if t in {"DATE", "TIME"} or t.startswith("TIMESTAMP"):
        return "temporal"
    if t == "BOOLEAN":
        return "bool"
    if t in {"VARCHAR", "TEXT", "STRING"}:
        return "text"
    return "other"


def _like(value: Any, mode: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {"contains": f"%{escaped}%", "starts": f"{escaped}%", "ends": f"%{escaped}"}[mode]


def _filter_sql(f: ColumnFilter, types: dict[str, str]) -> tuple[str, list[Any]]:
    if f.column not in types:
        raise DataError(f"colonne inconnue : {f.column}")
    t, cat, c = types[f.column], category(types[f.column]), _q(f.column)
    cast = f"TRY_CAST(? AS {t})"
    if f.op == "null":
        return f"{c} IS NULL", []
    if f.op == "not_null":
        return f"{c} IS NOT NULL", []
    if cat == "other" and f.op not in ("contains", "not_contains", "starts", "ends"):
        raise DataError(f"filtre « {f.op} » impossible sur la colonne {f.column} ({t})")
    if f.op in ("contains", "starts", "ends"):
        return f"CAST({c} AS VARCHAR) ILIKE ? ESCAPE '\\'", [_like(f.value, f.op)]
    if f.op == "not_contains":
        return f"(CAST({c} AS VARCHAR) NOT ILIKE ? ESCAPE '\\' OR {c} IS NULL)", [_like(f.value, "contains")]
    if f.op == "equals":
        return (f"{c} = ?", [str(f.value)]) if cat == "text" else (f"{c} = {cast}", [f.value])
    if f.op == "not_equals":
        return (f"{c} <> ?", [str(f.value)]) if cat == "text" else (f"{c} <> {cast}", [f.value])
    if f.op in ("gt", "gte", "lt", "lte"):
        sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.op]
        return f"{c} {sym} {cast}", [f.value]
    if f.op == "between":
        return f"{c} BETWEEN {cast} AND {cast}", [f.value, f.value2]
    if f.op == "in":
        if not f.values:
            return "FALSE", []
        return f"{c} IN ({', '.join([cast] * len(f.values))})", list(f.values)
    raise DataError(f"opérateur inconnu : {f.op}")


def _json_value(v: Any) -> Any:
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return None
    if isinstance(v, int | str | bool) or v is None:
        return v
    return str(v)  # dates, heures, listes, structures


# ------------------------------------------------------------------------------------------ connexion


class Duck:
    """Une connexion DuckDB par thread, verrouillée sur le dossier du cache (et sur le seul fichier `file` en plus :
    celui que désigne un port, ADR 0043)."""

    def __init__(self, root: Path, file: Path | None = None) -> None:
        self.root = root.resolve()
        self.file = file
        self._local = threading.local()
        self._files: OrderedDict[Path, Duck] = OrderedDict()
        self._files_guard = threading.Lock()

    def for_file(self, data: Path) -> Duck:
        """Les connexions qui lisent `data` : celles-ci s'il est dans le cache, sinon celles qui l'autorisent aussi."""
        if self.root in data.parents:
            return self
        with self._files_guard:
            found = self._files.pop(data, None) or Duck(self.root, data)
            self._files[data] = found
            while len(self._files) > 16:
                self._files.popitem(last=False)
            return found

    def con(self) -> duckdb.DuckDBPyConnection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = duck.connect("inspect")  # l'inspecteur ne doit pas prendre la mémoire des runs
            duck.load_spatial(con)
            con.execute(f"SET allowed_directories=[{_lit(self.root.as_posix())}]")
            if self.file is not None:
                con.execute(f"SET allowed_paths=[{_lit(self.file.as_posix())}]")
            con.execute("SET enable_external_access=false")
            con.execute("SET lock_configuration=true")
            self._local.con = con
        return con

    def run(
        self, sql: str, params: list[Any] | None = None, timeout: float | None = None
    ) -> tuple[list[str], list[tuple]]:
        with self.guarded(timeout) as con:
            cur = con.execute(sql, params or [])
            return [d[0] for d in cur.description], cur.fetchall()

    def arrow(self, sql: str, params: list[Any] | None = None) -> pa.Table:
        with self.guarded() as con:
            return con.sql(sql, params=params or []).to_arrow_table()

    @contextmanager
    def guarded(self, timeout: float | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
        """La connexion du thread, pour des requêtes au plus `timeout` secondes (`QUERY_TIMEOUT`) ; erreurs de DuckDB
        rendues en `DataError` (trop longue : 504, mémoire ou disque : 507)."""
        con = self.con()
        timer = threading.Timer(timeout or QUERY_TIMEOUT, con.interrupt)
        timer.start()
        try:
            yield con
        except duckdb.InterruptException:
            raise DataError("requête trop longue : affinez les filtres", 504) from None
        except duckdb.OutOfMemoryException:
            raise DataError(
                "mémoire insuffisante pour lire ces données : des géométries très volumineuses (entités fusionnées ?)",
                OUT_OF_MEMORY,
            ) from None
        except duckdb.Error as exc:
            if isinstance(found := duck.resource_error(exc), duck.DiskFull):
                raise DataError(str(found), OUT_OF_MEMORY) from None
            raise DataError(f"requête invalide : {str(exc).splitlines()[0]}") from None
        finally:
            timer.cancel()

    def scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        return self.run(sql, params)[1][0][0]


# ------------------------------------------------------------------------------------------ un port


@dataclass
class Port:
    duck: Duck
    run: str
    node: str
    name: str
    raw: Path  # place du port dans le run : ses vues sont rangées à côté
    data: Path  # fichier lu : celui du port, ou le fichier qu'il désigne (ADR 0043)
    crs: str | None
    has_geometry: bool
    total: int  # entités du port
    view: Path | None = None  # vue cartographique (morceaux par niveau), une fois prête
    entities: Path | None = None  # une ligne par entité : type, sommets, emprise, taille
    meta: dict[str, Any] | None = None
    map_status: Literal["ready", "building", "failed", "none"] = "none"
    map_error: str | None = None  # pourquoi la carte n'est pas affichée (« failed ») : `memory` ou l'erreur

    def src(self, geometry: bool = False) -> str:
        """Sous-requête SQL : attributs + `fid` (+ `geometry` typée GEOMETRY si `geometry`)."""
        base = (
            f"SELECT * EXCLUDE (file_row_number), file_row_number AS fid "
            f"FROM read_parquet({_lit(self.data.as_posix())}, file_row_number=true)"
        )
        if geometry and self.has_geometry:
            base = f"SELECT * REPLACE (ST_GeomFromWKB({GEOM}) AS {GEOM}) FROM ({base})"
        return f"({base})"

    def types(self) -> dict[str, str]:
        _, rows = self.duck.run(f"DESCRIBE SELECT * FROM {self.src()}")
        return {r[0]: r[1] for r in rows}

    def attributes(self, types: dict[str, str]) -> list[str]:
        return [c for c in types if c not in ("fid", GEOM)]


def open_port(duck: Duck, run: str, node: str, port: str, prepare: bool = True) -> Port:
    """Le port `run/node/port` ; sa vue cartographique est lancée en arrière-plan si besoin (`prepare`)."""
    for part in (run, node, port):
        if not SAFE_ID.match(part):
            raise DataError("identifiant invalide", 400)
    raw = (duck.root / run / node / f"{port}.parquet").resolve()
    if duck.root not in raw.parents:
        raise DataError("données introuvables : le cache a peut-être été purgé", 404)
    try:
        data = port_data(raw)
    except SourceChanged as exc:
        raise DataError(str(exc), 404) from None
    if data is None and sql_path(raw).is_file():
        data = _prepared(duck.root / run, node, port, raw)
    if data is None:
        raise DataError("données introuvables : le cache a peut-être été purgé", 404)
    file = pq.ParquetFile(data)
    schema, total = file.schema_arrow, file.metadata.num_rows
    found = Port(duck.for_file(data), run, node, port, raw, data, get_crs(schema), GEOM in schema.names, total)
    if prepare and found.has_geometry and found.crs:
        found.map_status, found.meta, found.map_error = _map_view(raw)
        if found.meta is not None:
            found.view, found.entities = views.view_path(raw), views.entities_path(raw)
            _prebuild(found)
    return found


def _prepared(run_dir: Path, node: str, port: str, raw: Path) -> Path:
    """Le fichier d'un port calculé à la demande, une fois écrit ; sinon lance son calcul en arrière-plan et lève
    `PREPARING` (l'interface redemande). Une erreur du calcul est rendue telle quelle, puis le calcul pourra être
    relancé (fichier lu modifié, mémoire…). Un calcul que l'interface ne redemande plus (autre port, autre nœud) est
    arrêté au bout de `ABANDONED_S` : choisir un nœud ouvre sa première sortie, qui peut être longue à calculer ; il
    reprend si on y revient."""
    with _data_guard:
        _data_asked[raw] = time.monotonic()
        job = _data_jobs.get(raw)
        if job is None or (job.done() and (job.exception() is not None or not raw.is_file())):
            exc = job.exception() if job is not None else None
            if exc is not None and not isinstance(exc, duckdb.InterruptException):
                del _data_jobs[raw]
                if isinstance(exc, SourceChanged):
                    raise DataError(str(exc), 404) from None
                found = duck.resource_error(exc)
                if isinstance(found, MemoryError):
                    raise DataError("mémoire insuffisante pour préparer ces données", OUT_OF_MEMORY) from None
                if found is not None:
                    raise DataError(str(found), OUT_OF_MEMORY) from None
                raise DataError(f"préparation impossible : {str(exc).splitlines()[0][:300]}", 500) from None

            def abandoned() -> bool:
                return time.monotonic() - _data_asked[raw] > ABANDONED_S

            job = _data_jobs[raw] = _DATA_BUILDS.submit(materialize, run_dir, node, port, "inspect", abandoned)
    if not job.done():
        raise DataError("préparation des données…", PREPARING)
    found = job.result()
    if found is None:
        raise DataError("données introuvables : le cache a peut-être été purgé", 404)
    return found


def _map_view(raw: Path) -> tuple[Literal["ready", "building", "failed", "none"], dict | None, str | None]:
    """État de la vue cartographique de `raw` : prête (ses métadonnées), en préparation (lancée au besoin), en échec
    (pourquoi : `memory` pour des géométries trop volumineuses, sinon l'erreur). Un échec reste noté jusqu'au
    redémarrage : on ne relance pas un calcul voué à échouer. Une vue demandée passe devant celles qui attendent
    encore leur tour (elles sont relancées à la prochaine ouverture de leur port) ; celles commencées vont au bout."""
    meta = views.read_view_meta(raw)
    if meta is not None:
        return "ready", meta, None
    with _builds_guard:
        job = _builds.get(raw)
        if job is not None and (
            job.cancelled() or (job.done() and job.exception() is None and job.result() is not None)
        ):
            job = None  # écartée par un port ouvert après elle, ou faite puis effacée depuis : à (re)faire
        if job is None:
            for waiting in _builds.values():  # pas encore commencées : le port qu'on vient d'ouvrir passe avant
                waiting.cancel()
            job = _builds[raw] = _VIEW_BUILDS.submit(views.ensure_view, raw)
    if job.cancelled() or not job.done():  # écartée entre-temps : relancée à la prochaine demande
        return "building", None, None
    exc = job.exception()
    if exc is None:
        meta = job.result()
        return ("ready", meta, None) if meta is not None else ("none", None, None)
    if isinstance(exc, (duckdb.OutOfMemoryException, MemoryError)):
        return "failed", None, "memory"  # message clair côté interface
    return "failed", None, str(exc).splitlines()[0][:300]


def _where(port: Port, q: Query, types: dict[str, str]) -> tuple[str, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    for f in q.filters:
        sql, p = _filter_sql(f, types)
        parts.append(sql)
        params += p
    if q.search.strip():
        cols = [c for c in types if c not in ("fid", GEOM) and category(types[c]) != "other"]
        if cols:
            parts.append("(" + " OR ".join(f"CAST({_q(c)} AS VARCHAR) ILIKE ? ESCAPE '\\'" for c in cols) + ")")
            params += [_like(q.search.strip(), "contains")] * len(cols)
    if q.where.strip():
        try:
            check_single_expression(q.where)
        except ConfigError as exc:
            raise DataError(str(exc)) from None
        parts.append(f"({q.where})")
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def _order(q: Query, types: dict[str, str]) -> str:
    keys = []
    for s in q.sort:
        if s.column not in types or s.column == GEOM:
            raise DataError(f"tri impossible sur : {s.column}")
        keys.append(f"{_q(s.column)} {'DESC' if s.desc else 'ASC'} NULLS LAST")
    return ", ".join([*keys, "fid"])


def _needs_geometry(q: Query) -> bool:
    return bool(q.where.strip() and NEEDS_GEOMETRY.search(q.where))


# ------------------------------------------------------------------------------------------ opérations


def info(port: Port) -> dict[str, Any]:
    types = port.types()
    meta = port.meta or {}
    return {
        "run": port.run,
        "node": port.node,
        "port": port.name,
        "total": port.total,
        "columns": [{"name": c, "type": types[c], "category": category(types[c])} for c in port.attributes(types)],
        "geometry": (
            {
                "crs": port.crs,
                "types": meta.get("geometry_types", []),
                "extent_3857": meta.get("extent_3857"),
                "map": port.view is not None,
                "map_status": port.map_status,
                "map_error": port.map_error,
            }
            if port.has_geometry
            else None
        ),
    }


def rows(port: Port, q: Query) -> dict[str, Any]:
    types = port.types()
    geom = _needs_geometry(q)
    where, params = _where(port, q, types)
    src = port.src(geometry=geom)
    if where or q.sort:
        # les fid de la page d'abord (tri des seules clés), puis ses lignes : les attributs et le type de géométrie ne
        # sont lus que pour la page, pas pour toutes les lignes qui la précèdent (une page loin dans la table)
        _, found = port.duck.run(
            f"SELECT fid FROM {src}{where} ORDER BY {_order(q, types)} LIMIT {q.limit} OFFSET {q.offset}",
            params,
            timeout=TABLE_TIMEOUT,
        )
        fids = [int(r[0]) for r in found]
        page = f"fid IN ({', '.join(map(str, fids))})" if fids else "FALSE"
    else:  # ordre d'origine, sans filtre : la ligne n est l'entité n
        fids = list(range(q.offset, min(q.offset + q.limit, port.total)))
        page = f"fid >= {q.offset} AND fid < {q.offset + q.limit}"
    attributes = ", ".join(["fid", *(_q(c) for c in port.attributes(types))])

    def select(kind: str | None) -> tuple[list[str], list[tuple]]:
        extra = f", {kind} AS {GEOM}" if kind else ""
        return port.duck.run(f"SELECT {attributes}{extra} FROM {port.src()} WHERE {page}")

    if not port.has_geometry:
        columns, data = select(None)
    elif port.entities is not None:  # type de géométrie de chaque entité, noté dans la vue
        columns, data = select("NULL")
        kinds = _kinds(port, [r[0] for r in data])
        data = [(*r[:-1], kinds.get(r[0])) for r in data]
    else:  # type de géométrie affiché dans la table (pas la géométrie elle-même)
        try:
            columns, data = select(_wkb_kind(GEOM))
        except DataError as exc:
            if exc.status != OUT_OF_MEMORY:
                raise
            # géométries trop volumineuses pour être lues par l'inspecteur : la table reste, sans leur type
            columns, data = select("NULL")
    rank = {f: i for i, f in enumerate(fids)}
    data.sort(key=lambda r: rank[r[0]])
    total = port.total
    filtered = port.duck.scalar(f"SELECT count(*) FROM {src}{where}", params) if where else total
    return {
        "columns": columns,
        "total": int(total),
        "filtered": int(filtered),
        "rows": [[_json_value(v) for v in row] for row in data],
    }


def _kinds(port: Port, fids: list[int]) -> dict[int, str]:
    """Type de géométrie des entités `fids`, lu dans la vue (sans lire les géométries)."""
    if not fids or port.entities is None:
        return {}
    listed = ", ".join(str(int(f)) for f in fids)
    _, found = port.duck.run(
        f"SELECT fid, kind FROM read_parquet({_lit(port.entities.as_posix())}) WHERE fid IN ({listed})"
    )
    return {int(f): k for f, k in found}


def stats(port: Port, with_validity: bool = False) -> dict[str, Any]:
    types = port.types()
    out: dict[str, Any] = {}
    cols = [c for c in port.attributes(types) if category(types[c]) != "other"]
    if cols:
        # valeurs distinctes exactes tant que c'est raisonnable, approchées au-delà
        exact = port.total <= EXACT_DISTINCT_MAX_ROWS
        distinct = "count(DISTINCT {c})" if exact else "approx_count_distinct({c})"
        parts = ["count(*) AS n"]
        for i, c in enumerate(cols):
            parts += [
                f"min({_q(c)}) AS mn{i}",
                f"max({_q(c)}) AS mx{i}",
                f"{distinct.format(c=_q(c))} AS d{i}",
                f"count({_q(c)}) AS nn{i}",
            ]
        _, (row,) = port.duck.run(f"SELECT {', '.join(parts)} FROM {port.src()}")
        n = row[0]
        for i, c in enumerate(cols):
            mn, mx, distinct, nn = row[1 + 4 * i : 5 + 4 * i]
            out[c] = {
                "type": types[c],
                "min": _json_value(mn),
                "max": _json_value(mx),
                "distinct": int(distinct),
                "nulls": int(n - nn),
            }
    for c in port.attributes(types):
        out.setdefault(c, {"type": types[c], "min": None, "max": None, "distinct": None, "nulls": None})
    result: dict[str, Any] = {"columns": out}
    if port.has_geometry:
        if port.meta is not None:
            kinds = port.meta.get("geometry_types", [])
        else:
            try:
                _, (found,) = port.duck.run(
                    f"SELECT list(DISTINCT {_wkb_kind(GEOM)}) FROM {port.src()} WHERE {GEOM} IS NOT NULL"
                )
                kinds = sorted(found[0] or [])
            except DataError as exc:
                if exc.status != OUT_OF_MEMORY:
                    raise
                kinds = []
        g: dict[str, Any] = {"crs": port.crs, "types": kinds, "extent_3857": (port.meta or {}).get("extent_3857")}
        if with_validity:
            _, (r,) = port.duck.run(
                f"SELECT count(*) FILTER (WHERE {GEOM} IS NULL), count(*) FILTER (WHERE NOT ST_IsEmpty(g) AND NOT ST_IsValid(g)), "
                f"count(*) FILTER (WHERE ST_IsEmpty(g)) FROM (SELECT ST_GeomFromWKB({GEOM}) AS g, {GEOM} FROM {port.src()})"
            )
            g.update({"null": int(r[0]), "invalid": int(r[1]), "empty": int(r[2])})
        result["geometry"] = g
    return result


def values(port: Port, column: str, q: Query, limit: int = 50) -> list[dict[str, Any]]:
    types = port.types()
    if column not in types or column == GEOM:
        raise DataError(f"colonne inconnue : {column}")
    where, params = _where(port, q, types)
    _, data = port.duck.run(
        f"SELECT {_q(column)} AS v, count(*) AS n FROM {port.src(geometry=_needs_geometry(q))}{where} "
        f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {int(limit)}",
        params,
    )
    return [{"value": _json_value(v), "count": int(n)} for v, n in data]


def classes(port: Port, column: str, q: Query, count: int = 5) -> dict[str, Any]:
    """Classes d'effectifs égaux (quantiles) d'un attribut numérique, sur les lignes filtrées, pour colorer la carte :
    bornes de la plus petite à la plus grande valeur finie, prises parmi les valeurs (doublons retirés : peu de valeurs
    distinctes donnent moins de classes) ; nombre de lignes sans valeur. Approchées sur une grande table."""
    types = port.types()
    if column not in types or category(types[column]) != "numeric":
        raise DataError(f"classes impossibles sur : {column}")
    where, params = _where(port, q, types)
    x = f"{_q(column)}::DOUBLE"
    quantile = "quantile_disc" if port.total <= EXACT_DISTINCT_MAX_ROWS else "approx_quantile"
    probabilities = [i / count for i in range(1, count)]
    finite = f"FILTER (WHERE isfinite({x}))"
    _, ((lo, hi, cuts, nulls),) = port.duck.run(
        f"SELECT min({x}) {finite}, max({x}) {finite}, {quantile}({x}, {probabilities}) {finite}, "
        f"count(*) FILTER (WHERE {_q(column)} IS NULL) FROM {port.src(geometry=_needs_geometry(q))}{where}",
        params,
    )
    breaks = [] if lo is None else sorted({lo, *(cuts or []), hi})
    return {"breaks": [_json_value(b) for b in breaks], "nulls": int(nulls)}


def locate(port: Port, q: Query, fid: int) -> int | None:
    """Position (à partir de 0) de l'entité `fid` dans la liste filtrée et triée ; None si elle est exclue par les filtres.

    Sans tri ni filtre, c'est son fid. Sinon, ses clés de tri sont lues (une ligne), puis on compte les lignes qui la
    précèdent dans l'ordre de `_order` (nuls en dernier, puis fid) : un parcours de la table, pas son tri complet."""
    types = port.types()
    where, params = _where(port, q, types)
    if not where and not q.sort:
        return fid if 0 <= fid < port.total else None
    _order(q, types)  # contrôle des colonnes de tri
    terms: list[str] = []
    ties: list[str] = []
    for i, s in enumerate(q.sort):
        c, k = _q(s.column), f"t.__k{i}"
        first = f"(({k} IS NULL AND {c} IS NOT NULL) OR {c} {'>' if s.desc else '<'} {k})"
        terms.append(" AND ".join([*ties, first]))
        ties.append(f"{c} IS NOT DISTINCT FROM {k}")
    terms.append(" AND ".join([*ties, "fid < t.__fid"]))
    keys = "".join(f", {_q(s.column)} AS __k{i}" for i, s in enumerate(q.sort))
    cond = where.removeprefix(" WHERE ") or "TRUE"
    src = port.src(geometry=_needs_geometry(q))
    _, data = port.duck.run(
        f"WITH t AS (SELECT fid AS __fid{keys} FROM {src} WHERE ({cond}) AND fid = ?) "
        f"SELECT count(*) FILTER (WHERE {' OR '.join(f'({t})' for t in terms)}), count(*) FILTER (WHERE fid = t.__fid) "
        f"FROM {src}, t WHERE {cond}",
        [*params, fid, *params],
        timeout=TABLE_TIMEOUT,
    )
    before, found = data[0]
    return int(before) if found else None


def _wgs84(port: Port, geometry: str = GEOM) -> str:
    """Expression SQL : `geometry` (GEOMETRY) reprojetée en WGS84."""
    return f"ST_Transform({geometry}, {_lit(port.crs)}, {_lit(WGS84)}, true)"


def feature(port: Port, fid: int) -> dict[str, Any]:
    types = port.types()
    attrs = port.attributes(types)
    names, data = port.duck.run(
        f"SELECT {', '.join(_q(c) for c in (attrs or ['fid']))} FROM {port.src()} WHERE fid = ?", [fid]
    )
    if not data:
        raise DataError("entité introuvable", 404)
    result: dict[str, Any] = {
        "fid": fid,
        "attributes": {n: _json_value(v) for n, v in zip(names, data[0], strict=True)},
    }
    if port.has_geometry and port.crs:
        found = _generalized_geometry(port, fid)
        if found is None:
            _, rows = port.duck.run(
                f"SELECT ST_AsGeoJSON(w), ST_XMin(w), ST_YMin(w), ST_XMax(w), ST_YMax(w) FROM "
                f"(SELECT {_wgs84(port, 'ST_GeomFromWKB(geometry)')} AS w FROM {port.src()} WHERE fid = ?) "
                f"WHERE w IS NOT NULL",
                [fid],
            )
            if rows and rows[0][1] is not None:
                found = json.loads(rows[0][0]), [rows[0][1], rows[0][2], rows[0][3], rows[0][4]], False
        if found is not None:
            result["geometry"], result["bbox"], edges = found
            if edges:  # la carte trace le contour (les lignes de la collection), pas celui de chaque pièce
                result["outline"] = "edges"
    return result


def _to_wgs84(xy: np.ndarray) -> np.ndarray:
    """EPSG:3857 → longitude, latitude (formules de la sphère de Web Mercator), au dix-centimètre près."""
    r = WORLD / math.pi
    lon = np.degrees(xy[:, 0] / r)
    lat = np.degrees(2 * np.arctan(np.exp(xy[:, 1] / r)) - math.pi / 2)
    return np.round(np.c_[lon, lat], 6)


def _generalized_geometry(port: Port, fid: int) -> tuple[dict[str, Any], list[float], bool] | None:
    """Géométrie d'une entité énorme (parcelles fusionnées…) pour la sélection : lue dans un niveau généralisé de la
    vue, à la finesse de `FEATURE_PIXELS` pixels sur son emprise ; son emprise ; vrai si elle porte son contour à part
    (polygone découpé). None : entité ordinaire (lue telle quelle) ou pas de vue."""
    if port.view is None or port.entities is None:
        return None
    _, found = port.duck.run(
        f"SELECT npoints, bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax "
        f"FROM read_parquet({_lit(port.entities.as_posix())}) WHERE fid = ?",
        [fid],
    )
    if not found or found[0][0] is None or found[0][0] <= FEATURE_MAX_POINTS:
        return None
    _, *box = found[0]
    extent = max(box[2] - box[0], box[3] - box[1])
    finest = [lv for lv in views.LEVELS if extent / views.pixel(lv + 1) <= FEATURE_PIXELS]
    level = max(finest) if finest else min(views.LEVELS)
    found = port.duck.arrow(
        f"SELECT geom_3857, bbox, role FROM read_parquet({_lit(port.view.as_posix())}) WHERE lvl = ? AND fid = ?",
        [level, fid],
    )
    if found.num_rows == 0:
        return None
    wkb = found.column("geom_3857").to_numpy(zero_copy_only=False)
    drawn = np.array([w is not None for w in wkb], bool)
    role = found.column("role").to_numpy()
    edges = drawn & (role == views.EDGE)
    shapes = shapely.from_wkb(wkb[drawn & ~edges])
    if not drawn.all():  # morceaux de moins d'un pixel : des cellules, regroupées en surfaces
        cells = found.column("bbox").combine_chunks().filter(pa.array(~drawn))
        cx, cy = ((cells.field(f"{a}min").to_numpy() + cells.field(f"{a}max").to_numpy()) / 2 for a in "xy")
        shapes = np.concatenate([shapes, _cells(cx, cy, extent / FEATURE_PIXELS)])
    if edges.any():  # polygone découpé : ses pièces (sans contour) et son contour à part, sans les coupes
        shapes = np.concatenate([shapes, [shapely.multilinestrings(shapely.get_parts(shapely.from_wkb(wkb[edges])))]])
    shapes = shapely.transform(shapes, _to_wgs84)
    polygons = shapes[shapely.get_type_id(shapes) == 3]
    geometry = shapely.multipolygons(polygons) if len(polygons) == len(shapes) else shapely.geometrycollections(shapes)
    corners = _to_wgs84(np.array([[box[0], box[1]], [box[2], box[3]]]))
    return json.loads(shapely.to_geojson(geometry)), [*corners[0], *corners[1]], bool(edges.any())


def _cells(cx: np.ndarray, cy: np.ndarray, size: float) -> np.ndarray:
    """Polygones qui couvrent les cellules (côté `size`) des points `cx, cy` : cellules voisines d'une rangée en
    rectangles, rectangles voisins fusionnés (ils se touchent sans se chevaucher : union de couverture, rapide)."""
    ij = np.unique(np.c_[np.floor(cy / size), np.floor(cx / size)].astype(np.int64), axis=0)  # par rangée, puis colonne
    j, i = ij[:, 0], ij[:, 1]
    start = np.r_[True, (j[1:] != j[:-1]) | (i[1:] != i[:-1] + 1)]
    i0, i1, jr = i[start], np.maximum.reduceat(i, np.flatnonzero(start)), j[start]
    rects = shapely.box(i0 * size, jr * size, (i1 + 1) * size, (jr + 1) * size)
    try:
        merged = shapely.coverage_union_all(rects)
    except shapely.errors.GEOSException:
        return rects
    return shapely.get_parts(merged)


def export_batches(port: Port, q: Query) -> Iterator[pa.RecordBatch]:
    """Lots Arrow de la vue filtrée (tous les attributs + géométrie d'origine) pour l'export ; le résultat est fermé
    même si l'export s'arrête (sinon la connexion du thread refuserait toute autre requête)."""
    types = port.types()
    geom = _needs_geometry(q)
    where, params = _where(port, q, types)
    cols = [_q(c) for c in port.attributes(types)]
    if port.has_geometry:  # la clause WHERE a pu typer la géométrie en GEOMETRY : on la rend en WKB
        cols.append(f"ST_AsWKB({GEOM}) AS {GEOM}" if geom else GEOM)
    sql = f"SELECT {', '.join(cols)} FROM {port.src(geometry=geom)}{where} ORDER BY {_order(q, types)}"
    with port.duck.con().sql(sql, params=params).to_arrow_reader(50_000) as reader:
        yield from reader


# ------------------------------------------------------------------------------------------ tuiles


def _need_map(port: Port, part: Path | None) -> None:
    if part is None and port.map_status == "building":
        raise DataError("la carte de ce port est en préparation", 409)
    if part is None:
        raise DataError(
            "pas de vue cartographique pour ce port (géométrie absente ou système de coordonnées inconnu)", 404
        )


def _size_class(column: str) -> str:
    """Classe de taille d'une entité (quart de puissance de deux, en mètres) ; NULL pour un point (taille nulle)."""
    return f"CASE WHEN {column} > 0 THEN floor(4 * log2({column}))::INTEGER END"


def _size_classes(port: Port, q: Query, box: list[float]) -> list[tuple[int | None, int]]:
    """Entités dont l'emprise touche `box` (EPSG:3857 : xmin, xmax, ymin, ymax), filtres compris, par classe de taille.
    Les entités de la vue sont triées selon Hilbert : une petite zone ne lit que quelques row groups."""
    assert port.entities is not None
    where, params = _where(port, q, port.types())
    join = f" JOIN (SELECT fid FROM {port.src(geometry=_needs_geometry(q))}{where}) a USING (fid)" if where else ""
    _, classes = port.duck.run(
        f"SELECT {_size_class('e.esize')} AS k, count(*) FROM read_parquet({_lit(port.entities.as_posix())}) e{join} "
        "WHERE e.bbox.xmax >= ? AND e.bbox.xmin <= ? AND e.bbox.ymax >= ? AND e.bbox.ymin <= ? GROUP BY k",
        [*params, *box],
    )
    return [(None if k is None else int(k), int(n)) for k, n in classes]


def _choose(classes: list[tuple[int | None, int]]) -> tuple[int | None, int]:
    """Entités à dessiner parmi `classes`, au plus `MAP_LIMIT` : (classe de taille minimale, un point sur `every`).

    Les plus grandes (lignes et surfaces) d'abord ; les points, sans taille, attendent qu'on zoome. Si les lignes et
    surfaces tiennent dans la limite, les points sont échantillonnés : un sur `every` (paliers 1, 2, 3, 4, 6, 8, 12…),
    choisi d'après le seul `fid` (le même d'une tuile et d'un zoom à l'autre). `every` 0 : aucun point.
    """
    total = sum(n for _, n in classes)
    points = sum(n for k, n in classes if k is None)
    if total <= MAP_LIMIT:
        return None, 1
    if total - points >= MAP_LIMIT:
        min_size, shown = None, 0
        for k, n in sorted(((k, n) for k, n in classes if k is not None), reverse=True):
            if shown and shown + n > MAP_LIMIT:
                break
            min_size, shown = k, shown + n
        return min_size, 0
    room, every, step = MAP_LIMIT - (total - points), 1, 0
    while points > every * room and every < MAX_EVERY:
        every = 3 * 2 ** (step // 2) if step % 2 else 2 ** (step // 2 + 1)  # 2, 3, 4, 6, 8, 12, 16, 24…
        step += 1
    return None, every


def _tile_box(z: int, x: int, y: int) -> list[float]:
    size = 2 * WORLD / 2**z
    return [-WORLD + x * size, -WORLD + (x + 1) * size, WORLD - (y + 1) * size, WORLD - y * size]


def _tiles_dir(port: Port) -> Path:
    return port.duck.root / port.run / "_tiles" / f"{port.node}__{port.name}"


def _selection(port: Port, q: Query, z: int, x: int, y: int) -> tuple[int | None, int]:
    """Entités dessinées dans la tuile `z/x/y` : choisies pour toute sa métatuile (4 × 4 tuiles), donc les mêmes dans
    les tuiles voisines, et indépendantes de la vue : une tuile a toujours le même contenu (le navigateur et le disque
    la gardent), déplacer ou zoomer la carte ne recharge rien."""
    assert port.view is not None
    m = max(z - META, 0)
    mx, my = x >> (z - m), y >> (z - m)
    stamp = [port.view.name, port.view.stat().st_mtime_ns, MAP_LIMIT]
    key = hashlib.sha1(json.dumps([stamp, q.filter_key(), z, mx, my], sort_keys=True).encode()).hexdigest()[:20]
    with _selections_guard:
        if key in _selections:
            _selections.move_to_end(key)
            return _selections[key]
        lock = _selection_locks.setdefault(key, threading.Lock())
    with lock:  # deux tuiles de la même métatuile ne comptent qu'une fois
        if key in _selections:
            return _selections[key]
        saved = _tiles_dir(port) / "_selections" / f"{key}.json"
        try:
            chosen = tuple(json.loads(saved.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            chosen = _choose(_size_classes(port, q, _tile_box(m, mx, my)))
            write_atomically(saved, json.dumps(chosen).encode())
        with _selections_guard:
            _selections[key] = chosen  # type: ignore[assignment]
            while len(_selections) > 4096:
                _selections.popitem(last=False)
            _selection_locks.pop(key, None)
    return chosen  # type: ignore[return-value]


def tile_cache_file(port: Port, z: int, x: int, y: int, q: Query, color: str | None) -> Path:
    stamp = [port.view.name, port.view.stat().st_mtime_ns, MAP_LIMIT] if port.view else 0
    key = hashlib.sha1(json.dumps([q.filter_key(), color, stamp], sort_keys=True).encode()).hexdigest()[:16]
    return _tiles_dir(port) / key / str(z) / str(x) / f"{y}.pbf"


def write_atomically(target: Path, data: bytes) -> None:
    """Écrit `data` dans `target` sans qu'un lecteur voie un fichier partiel, même si deux requêtes écrivent la même
    tuile en même temps : chacune a son fichier temporaire (un nom commun faisait échouer la seconde en 500)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def _tile_shapes(wkb: np.ndarray, x0: float, y1: float, size: float) -> tuple[np.ndarray, np.ndarray]:
    """Morceaux EPSG:3857 → coordonnées entières de la tuile (y vers le bas), prêtes à encoder ; et lesquels garder.

    Calcul vectorisé, sans réparation (inutile pour dessiner) : `ST_AsMVTGeom` réparait chaque polygone replié par
    l'arrondi, ~1 ms par entité à faible zoom. Un polygone plus petit qu'un pixel devient un carré d'un pixel (il reste
    visible ; un seul par pixel) ; les autres sont simplifiés à deux unités près, les gros anneau par anneau
    (`simplify_polygons` : `shapely.simplify` valide et répare chaque polygone simplifié). Anneaux orientés selon la
    spécification MVT. Toujours des morceaux (jamais un multipolygone entier) : simplifier d'un bloc un multipolygone
    de 300 000 morceaux prend des minutes à GEOS.
    """
    scale = EXTENT / size
    g = shapely.from_wkb(wkb)
    g = shapely.transform(g, lambda c: np.c_[(c[:, 0] - x0) * scale, (y1 - c[:, 1]) * scale])
    g = shapely.clip_by_rect(g, -TILE_BUFFER, -TILE_BUFFER, EXTENT + TILE_BUFFER, EXTENT + TILE_BUFFER)
    b = shapely.bounds(g)
    with np.errstate(invalid="ignore"):
        tiny = (shapely.get_dimensions(g) == 2) & (np.fmax(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) < PIXEL)
    cx, cy = np.round((b[tiny, 0] + b[tiny, 2]) / 2), np.round((b[tiny, 1] + b[tiny, 3]) / 2)
    half = PIXEL / 2
    g[tiny] = shapely.box(cx - half, cy - half, cx + half, cy + half)
    heavy = ~tiny & np.isin(shapely.get_type_id(g), (3, 6)) & (shapely.get_num_coordinates(g) > HEAVY_POLYGON)
    g[heavy] = views.simplify_polygons(g[heavy], 2.0)
    g[~tiny & ~heavy] = shapely.simplify(g[~tiny & ~heavy], 2.0, preserve_topology=False)
    g = shapely.set_precision(g, 1.0, mode="pointwise")
    keep = ~(shapely.is_missing(g) | shapely.is_empty(g))
    if tiny.any():  # un seul carré par pixel : une zone dense de petits morceaux ne se répète pas
        _, first = np.unique(np.c_[cx, cy], axis=0, return_index=True)
        repeated = np.ones(len(cx), bool)
        repeated[first] = False
        keep[np.flatnonzero(tiny)[repeated]] = False
    return shapely.orient_polygons(g, exterior_cw=False), keep


def tile(
    port: Port,
    z: int,
    x: int,
    y: int,
    q: Query,
    color: str | None = None,
    abandoned: Callable[[], bool] = lambda: False,
    ahead: bool = False,
) -> bytes:
    """Tuile MVT compressée (gzip) des entités choisies pour sa métatuile (voir `_selection`). `abandoned` : plus
    personne ne l'attend (`Abandoned`, sans calcul s'il en est encore temps) ; `ahead` : calculée d'avance, après
    celles que la carte attend."""
    _need_map(port, port.view)
    if not (0 <= z <= 22 and 0 <= x < 2**z and 0 <= y < 2**z):
        raise DataError("tuile hors pyramide", 400)
    cache = tile_cache_file(port, z, x, y, q, color)
    if cache.is_file():
        return cache.read_bytes()
    if abandoned():
        raise Abandoned
    min_size, every = _selection(port, q, z, x, y)
    with _GATE.slot(abandoned, ahead):
        data = gzip.compress(_make_tile(port, z, x, y, q, color, every, min_size), compresslevel=5, mtime=0)
    write_atomically(cache, data)
    return data


def _prebuild(port: Port) -> None:
    """Calcule d'avance, en arrière-plan, les tuiles sans filtre des petites échelles (les plus chères) : la carte
    les lit ensuite sur le disque. Une fois par vue, pour le dernier port ouvert : celui d'avant s'arrête (il reprend,
    sans refaire ses tuiles, à sa prochaine ouverture)."""
    global _ahead_of
    assert port.view is not None and port.meta is not None
    key = (port.view, port.view.stat().st_mtime_ns)
    with _builds_guard:
        if key in _prebuilt:
            return
        _ahead_of = key
        if key in _prebuilding:
            return
        _prebuilding.add(key)
    _PREBUILDS.submit(_prebuild_tiles, port, key)


def prebuild_tiles(extent: list[float]) -> list[tuple[int, int, int]]:
    """Tuiles qui touchent `extent` (EPSG:3857), du zoom 0 au plus `PREBUILD_MAX_ZOOM`, `PREBUILD_MAX_TILES` au plus
    (zoom par zoom : on s'arrête avant le premier qui dépasse)."""
    found: list[tuple[int, int, int]] = []
    for z in range(PREBUILD_MAX_ZOOM + 1):
        span = 2 * WORLD / 2**z
        cols = range(max(0, int((extent[0] + WORLD) // span)), min(2**z - 1, int((extent[2] + WORLD) // span)) + 1)
        rows = range(max(0, int((WORLD - extent[3]) // span)), min(2**z - 1, int((WORLD - extent[1]) // span)) + 1)
        if len(found) + len(cols) * len(rows) > PREBUILD_MAX_TILES:
            break
        found += [(z, x, y) for x in cols for y in rows]
    return found


def _prebuild_tiles(port: Port, key: tuple[Path, int]) -> None:
    assert port.meta is not None
    try:
        for z, x, y in prebuild_tiles(port.meta["extent_3857"]):
            if not has_port(port.raw):  # cache libéré entre-temps : on s'arrête
                return
            tile(port, z, x, y, Query(), abandoned=lambda: _ahead_of != key, ahead=True)
        with _builds_guard:
            _prebuilt.add(key)
    except Abandoned:
        pass  # un autre port est ouvert : il passe avant
    except Exception:  # noqa: BLE001 - la carte la recalculera à la demande
        with _builds_guard:
            _prebuilt.add(key)
    finally:
        with _builds_guard:
            _prebuilding.discard(key)


def _make_tile(
    port: Port, z: int, x: int, y: int, q: Query, color: str | None, every: int = 1, min_size: int | None = None
) -> bytes:
    assert port.view is not None
    types = port.types()
    if color and (color not in types or color == GEOM or category(types[color]) == "other"):
        raise DataError(f"coloration impossible sur : {color}")
    where, params = _where(port, q, types)
    size = 2 * WORLD / 2**z
    x0, y1 = -WORLD + x * size, WORLD - y * size  # coin haut gauche de la tuile (EPSG:3857)
    # morceaux du niveau de ce zoom dans l'emprise de la tuile : le tri de la vue (niveau, Hilbert) et les
    # statistiques des row groups sur `lvl` et `bbox` évitent de lire le reste
    spatial = (
        f"v.lvl IN ({views.level_for(z)}, {views.POINTS}) "
        "AND v.bbox.xmax >= ? AND v.bbox.xmin <= ? AND v.bbox.ymax >= ? AND v.bbox.ymin <= ?"
    )
    box = [x0, x0 + size, y1 - size, y1]
    join = ""
    if where or color:  # filtres de la table, couleur : attributs lus dans le port
        colour = f", {_q(color)} AS c" if color else ""
        join = f" JOIN (SELECT fid{colour} FROM {port.src(geometry=_needs_geometry(q))}{where}) a USING (fid)"
    if min_size is not None:  # métatuile trop chargée : les plus grandes entités (les mêmes dans toutes ses tuiles)
        spatial += f" AND {_size_class('v.esize')} >= {int(min_size)}"
    elif every != 1:  # points échantillonnés (le même échantillon dans toutes les tuiles), ou aucun
        spatial += f" AND (v.esize > 0 OR hash(v.fid) % {int(every)} = 0)" if every else " AND v.esize > 0"
    source = f"read_parquet({_lit(port.view.as_posix())}) v{join} WHERE {spatial}"
    count = port.duck.scalar(f"SELECT count(*) FROM {source} AND v.geom_3857 IS NOT NULL", [*params, *box])
    sample = f" AND hash(v.fid, v.bbox.xmin) % {math.ceil(count / TILE_CAP)} = 0" if count > TILE_CAP else ""
    c = ", a.c AS c" if color else ""
    found = port.duck.arrow(
        f"SELECT v.fid AS fid{c}, v.role, v.geom_3857 FROM {source} AND v.geom_3857 IS NOT NULL{sample}",
        [*params, *box],
    )
    shapes, keep = _tile_shapes(found.column("geom_3857").to_numpy(zero_copy_only=False), x0, y1, size)
    rows = found.drop_columns(["geom_3857"]).filter(pa.array(keep))
    wkb = [shapely.to_wkb(shapes[keep])]
    # morceaux de moins d'un pixel (cellules de la vue, sans géométrie) : un carré d'un pixel par pixel occupé
    px = size / (EXTENT / PIXEL)
    c = ", arg_min(a.c, v.fid) AS c" if color else ""
    dots = port.duck.arrow(
        f"""SELECT min(v.fid) AS fid{c}, {views.PART}::UTINYINT AS role,
                   floor(((v.bbox.xmin + v.bbox.xmax) / 2 - {x0}) / {px})::INTEGER AS i,
                   floor(({y1} - (v.bbox.ymin + v.bbox.ymax) / 2) / {px})::INTEGER AS j
            FROM {source} AND v.geom_3857 IS NULL GROUP BY i, j""",
        [*params, *box],
    )
    if dots.num_rows:
        i, j = (dots.column(k).to_numpy().astype(float) * PIXEL for k in ("i", "j"))
        squares = shapely.orient_polygons(shapely.box(i, j, i + PIXEL, j + PIXEL), exterior_cw=False)
        rows = pa.concat_tables([rows, dots.drop_columns(["i", "j"]).cast(rows.schema)])
        wkb.append(shapely.to_wkb(squares))
    rows = rows.append_column("wkb", pa.array(np.concatenate(wkb), pa.binary()))
    name = f"tile_{uuid.uuid4().hex[:8]}"
    prop = ", 'c': c" if color else ""
    with port.duck.guarded() as con:
        con.register(name, rows)
        try:
            # `r` : surface (1) ou contour (2) d'un polygone découpé (la carte dessine le contour à part) ; absent
            # pour un morceau ordinaire (ST_AsMVT omet une propriété NULL)
            blob = con.execute(
                f"SELECT ST_AsMVT({{'geom': ST_GeomFromWKB(wkb), 'fid': fid, "
                f"'r': NULLIF(role, {views.PART})::INTEGER{prop}}}, 'data', {EXTENT}, 'geom', 'fid') FROM {name}"
            ).fetchall()[0][0]  # lu jusqu'au bout : DuckDB 2 refuse `con.sql` après un résultat inachevé
        finally:
            con.unregister(name)
    return bytes(blob) if blob else b""
