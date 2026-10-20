"""PostgreSQL / PostGIS : lecture par curseur serveur (lots Arrow) et écriture par COPY.

Les connexions sont nommées (voir `etl_engine.connections`). La lecture se fait dans une transaction en lecture
seule. La géométrie est échangée en WKB ; le SRID de la colonne devient le SCR du flux.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
import pyarrow as pa
import pyproj
import shapely
from psycopg import sql

from etl_engine import connections
from etl_engine.data import GEOM, get_crs, has_geometry, with_crs
from etl_engine.sdk import ConfigError

GEOMETRY_TYPES = ("geometry", "geography")

# oid PostgreSQL -> type Arrow (les types non listés sont lus en texte)
_SCALARS: dict[int, pa.DataType] = {
    16: pa.bool_(),
    21: pa.int16(),
    23: pa.int32(),
    20: pa.int64(),
    26: pa.int64(),  # oid
    700: pa.float32(),
    701: pa.float64(),
    1700: pa.float64(),  # numeric : lu en flottant (voir docs/formats.md)
    25: pa.string(),
    1043: pa.string(),
    1042: pa.string(),
    19: pa.string(),
    18: pa.string(),
    114: pa.string(),
    3802: pa.string(),
    2950: pa.string(),
    1082: pa.date32(),
    1114: pa.timestamp("us"),
    1184: pa.timestamp("us", tz="UTC"),
    1083: pa.time64("us"),
    17: pa.binary(),
}
_SAME_AS_TEXT = {114, 3802, 2950}  # json, jsonb, uuid : la valeur Python n'est pas une chaîne


def split_table(name: str) -> tuple[str, str]:
    """« schema.table » -> (schema, table) ; sans schéma : public."""
    parts = [p.strip().strip('"') for p in name.split(".")]
    if len(parts) == 1 and parts[0]:
        return "public", parts[0]
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    raise ConfigError(f"nom de table invalide : « {name} » (attendu : schéma.table)")


def connect(name: str, read_only: bool = False) -> psycopg.Connection:
    info = connections.resolve(name)
    try:
        conn = psycopg.connect(info.conninfo(), connect_timeout=15)
    except psycopg.OperationalError as exc:
        raise ConfigError(f"connexion « {name} » impossible : {_first_line(exc)}") from None
    if read_only:
        conn.read_only = True
    return conn


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__


# ----------------------------------------------------------------------------------------- lecture


@dataclass
class _Column:
    name: str
    oid: int
    typname: str
    elem: int
    arrow: pa.DataType | None  # None : colonne lue en texte
    is_geom: bool
    is_array: bool


def _describe(conn: psycopg.Connection, inner: sql.Composable) -> list[_Column]:
    with conn.cursor() as cur:
        try:
            cur.execute(sql.SQL("SELECT * FROM ({}) AS _q LIMIT 0").format(inner))
        except psycopg.errors.Error as exc:
            raise ConfigError(f"requête ou table invalide : {_first_line(exc)}") from None
        desc = [(d.name, d.type_code) for d in cur.description or []]
        oids = sorted({oid for _, oid in desc})
        cur.execute("SELECT oid, typname, typelem, typcategory FROM pg_type WHERE oid = ANY(%s)", (oids,))
        types = {row[0]: row for row in cur.fetchall()}
    out = []
    for name, oid in desc:
        _, typname, elem, category = types[oid]
        is_array = category == "A"
        base = _SCALARS.get(elem if is_array else oid)
        out.append(
            _Column(
                name=name,
                oid=oid,
                typname=typname,
                elem=elem,
                arrow=pa.list_(base) if is_array and base is not None else base,
                is_geom=typname in GEOMETRY_TYPES,
                is_array=is_array,
            )
        )
    return out


def _inner(table: str, query: str) -> sql.Composable:
    if query.strip():
        return sql.SQL(query.strip().rstrip(";"))  # type: ignore[arg-type]
    schema, name = split_table(table)
    return sql.SQL("SELECT * FROM {}").format(sql.Identifier(schema, name))


def _geometry_column(cols: list[_Column], wanted: str) -> _Column | None:
    geoms = [c for c in cols if c.is_geom]
    if wanted:
        for c in geoms:
            if c.name == wanted:
                return c
        raise ConfigError(f"colonne géométrique « {wanted} » introuvable")
    return geoms[0] if geoms else None


def _arrow_schema(cols: list[_Column], geom: _Column | None, crs: str | None) -> pa.Schema:
    fields = []
    for c in cols:
        if geom is not None and c.name == geom.name:
            fields.append(pa.field(GEOM, pa.binary()))
        else:
            fields.append(pa.field(c.name, c.arrow or pa.string()))
    return with_crs(pa.schema(fields), crs)


_schema_cache: dict[tuple, tuple[float, pa.Schema]] = {}


def _srid(conn: psycopg.Connection, inner: sql.Composable, geom: _Column) -> int:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT ST_SRID({c}::geometry) FROM ({q}) AS _q WHERE {c} IS NOT NULL LIMIT 1").format(
                c=sql.Identifier(geom.name), q=inner
            )
        )
        row = cur.fetchone()
    return int(row[0]) if row and row[0] else 0


def _crs_for(srid: int, override: str | None) -> str | None:
    if override:
        return override
    return f"EPSG:{srid}" if srid else None


def read_schema(
    connection: str,
    table: str = "",
    query: str = "",
    geometry_column: str = "",
    crs: str | None = None,
    **_: Any,
) -> pa.Schema:
    key = (connection, table, query, geometry_column, crs)
    cached = _schema_cache.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    with connect(connection, read_only=True) as conn:
        inner = _inner(table, query)
        cols = _describe(conn, inner)
        geom = _geometry_column(cols, geometry_column)
        srid = _srid(conn, inner, geom) if geom is not None and not crs else 0
    schema = _arrow_schema(cols, geom, _crs_for(srid, crs))
    _schema_cache[key] = (time.monotonic(), schema)
    return schema


def _as_text(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, dict | list) else str(value)
    )


def _to_arrow(rows: list[tuple], cols: list[_Column], schema: pa.Schema) -> pa.Table:
    arrays = []
    for i, field in enumerate(schema):
        values = [r[i] for r in rows]
        col = cols[i]
        if col.oid == 1700 and not col.is_array:
            values = [None if v is None else float(v) for v in values]
        elif col.oid in _SAME_AS_TEXT or (col.is_array and col.elem in _SAME_AS_TEXT):
            values = [None if v is None else (v if isinstance(v, str) else _as_text(v)) for v in values]
        elif col.arrow is None and not col.is_geom:
            values = [None if v is None else str(v) for v in values]
        arrays.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def read_batches(
    connection: str,
    batch_rows: int,
    table: str = "",
    query: str = "",
    where: str = "",
    geometry_column: str = "",
    crs: str | None = None,
    **_: Any,
) -> Iterator[pa.Table]:
    with connect(connection, read_only=True) as conn:
        inner = _inner(table, query)
        cols = _describe(conn, inner)
        geom = _geometry_column(cols, geometry_column)
        srid = _srid(conn, inner, geom) if geom is not None and not crs else 0
        schema = _arrow_schema(cols, geom, _crs_for(srid, crs))
        exprs: list[sql.Composable] = []
        for c in cols:
            ident = sql.Identifier(c.name)
            if geom is not None and c.name == geom.name:
                exprs.append(sql.SQL("ST_AsBinary({}::geometry)").format(ident))
            elif c.is_geom:
                exprs.append(sql.SQL("ST_AsText({}::geometry)").format(ident))
            elif c.arrow is None:
                exprs.append(sql.SQL("{}::text").format(ident))
            else:
                exprs.append(ident)
        stmt = sql.SQL("SELECT {} FROM ({}) AS _q").format(sql.SQL(", ").join(exprs), inner)
        if where.strip():
            stmt += sql.SQL(" WHERE ({})").format(sql.SQL(where.strip()))  # type: ignore[arg-type]
        with conn.cursor(name="etl_read") as cur:
            cur.itersize = batch_rows
            try:
                cur.execute(stmt)
                while rows := cur.fetchmany(batch_rows):
                    yield _to_arrow(rows, cols, schema)
            except psycopg.errors.Error as exc:
                raise ConfigError(f"lecture PostgreSQL : {_first_line(exc)}") from None


def list_tables(connection: str) -> list[dict[str, Any]]:
    """Tables et vues lisibles (hors schémas système), avec leur colonne géométrique si elle existe."""
    with connect(connection, read_only=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT n.nspname, c.relname, c.relkind, c.reltuples::bigint,
                      (SELECT f_geometry_column FROM geometry_columns g
                        WHERE g.f_table_schema = n.nspname AND g.f_table_name = c.relname LIMIT 1)
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'v', 'm', 'p')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                ORDER BY 1, 2"""
            if _has_postgis(cur)
            else """SELECT n.nspname, c.relname, c.relkind, c.reltuples::bigint, NULL
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'v', 'm', 'p')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                ORDER BY 1, 2"""
        )
        return [
            {"schema": r[0], "table": r[1], "kind": r[2], "rows_estimate": max(r[3], 0), "geometry": r[4]}
            for r in cur.fetchall()
        ]


def _has_postgis(cur: psycopg.Cursor) -> bool:
    cur.execute("SELECT to_regclass('geometry_columns') IS NOT NULL")
    return bool(cur.fetchone()[0])


def check_connection(name: str) -> dict[str, Any]:
    """Essai de connexion : version du serveur et de PostGIS."""
    with connect(name, read_only=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        server = cur.fetchone()[0]
        postgis = None
        if _has_postgis(cur):
            cur.execute("SELECT postgis_lib_version()")
            postgis = cur.fetchone()[0]
    return {"server": server, "postgis": postgis}


# ---------------------------------------------------------------------------------------- écriture

_ARROW_TO_PG = {
    pa.types.is_boolean: "boolean",
    pa.types.is_int8: "smallint",
    pa.types.is_int16: "smallint",
    pa.types.is_int32: "integer",
    pa.types.is_int64: "bigint",
    pa.types.is_uint8: "smallint",
    pa.types.is_uint16: "integer",
    pa.types.is_uint32: "bigint",
    pa.types.is_uint64: "numeric(20,0)",
    pa.types.is_float16: "real",
    pa.types.is_float32: "real",
    pa.types.is_float64: "double precision",
    pa.types.is_date: "date",
    pa.types.is_time: "time",
    pa.types.is_decimal: "numeric",
    pa.types.is_binary: "bytea",
    pa.types.is_large_binary: "bytea",
}


def pg_type(t: pa.DataType) -> str:
    if pa.types.is_timestamp(t):
        return "timestamptz" if t.tz else "timestamp"
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return pg_type(t.value_type) + "[]"
    if pa.types.is_dictionary(t):
        return pg_type(t.value_type)
    for test, name in _ARROW_TO_PG.items():
        if test(t):
            return name
    return "text"


_WKB_NAMES = {
    0: "Point",
    1: "LineString",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}
_PROMOTE = {"Polygon": "MultiPolygon", "LineString": "MultiLineString", "Point": "MultiPoint"}


def _epsg(crs: str | None) -> int:
    if not crs:
        return 0
    try:
        return pyproj.CRS.from_user_input(crs).to_epsg() or 0
    except pyproj.exceptions.CRSError:
        return 0


def _ident(name: str) -> sql.Identifier:
    return sql.Identifier(name)


class PgWriter:
    """Écrit par COPY dans une transaction unique : tout ou rien. `abort()` annule, `close()` valide."""

    messages: list[str]

    def __init__(
        self,
        connection: str,
        table: str,
        mode: str = "append",
        key: list[str] | None = None,
        geometry_column: str = "geom",
        geometry_type: str = "auto",
        create_spatial_index: bool = True,
        crs: str | None = None,
        **_: Any,
    ) -> None:
        if mode == "upsert" and not key:
            raise ConfigError("le mode « upsert » demande au moins une colonne clé")
        self.connection, self.mode, self.key = connection, mode, list(key or [])
        self.schema_name, self.table_name = split_table(table)
        self.geometry_column, self.geometry_type = geometry_column, geometry_type
        self.create_spatial_index, self.crs = create_spatial_index, crs
        self.messages = []
        self._conn: psycopg.Connection | None = None
        self._created = False
        self._columns: list[str] | None = None  # colonnes du flux, dans l'ordre d'écriture
        self._srid = 0
        self._multi: str | None = None  # type Multi* visé : les géométries simples sont promues
        self._rows = 0
        self._has_geom = False

    # -- helpers
    @property
    def _target(self) -> sql.Identifier:
        return sql.Identifier(self.schema_name, self.table_name)

    def _exists(self, cur: psycopg.Cursor) -> bool:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f'"{self.schema_name}"."{self.table_name}"',))
        return bool(cur.fetchone()[0])

    def _geometry_ddl(self, table: pa.Table) -> sql.Composable:
        geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
        present = geoms[~shapely.is_missing(geoms)]
        kinds = {_WKB_NAMES.get(int(i), "Geometry") for i in shapely.get_type_id(present)}
        z = bool(len(present) and shapely.has_z(present).any())
        wanted = self.geometry_type
        if wanted == "auto":
            if kinds and kinds <= {"Polygon", "MultiPolygon"}:  # polygones et lignes : toujours en « Multi »
                wanted = "MultiPolygon"
            elif kinds and kinds <= {"LineString", "MultiLineString"}:
                wanted = "MultiLineString"
            elif len(kinds) == 1:
                wanted = next(iter(kinds))
            elif kinds and kinds <= {"Polygon", "MultiPolygon"}:
                wanted = "MultiPolygon"
            elif kinds and kinds <= {"LineString", "MultiLineString"}:
                wanted = "MultiLineString"
            elif kinds and kinds <= {"Point", "MultiPoint"}:
                wanted = "MultiPoint"
            else:
                wanted = "Geometry"
            if wanted in _PROMOTE.values():
                self._multi = wanted
        elif wanted in _PROMOTE.values():
            self._multi = wanted
        label = wanted + ("Z" if z and wanted != "Geometry" else "")
        return sql.SQL("geometry({}, {})").format(sql.SQL(label), sql.Literal(self._srid))

    def _create(self, cur: psycopg.Cursor, table: pa.Table) -> None:
        defs: list[sql.Composable] = []
        for field in table.schema:
            if field.name == GEOM:
                defs.append(sql.SQL("{} {}").format(_ident(self.geometry_column), self._geometry_ddl(table)))
            else:
                defs.append(sql.SQL("{} {}").format(_ident(field.name), sql.SQL(pg_type(field.type))))
        if self.key:
            missing = [k for k in self.key if k not in table.column_names]
            if missing:
                raise ConfigError(f"colonne clé absente des données : {', '.join(missing)}")
            defs.append(sql.SQL("PRIMARY KEY ({})").format(sql.SQL(", ").join(_ident(k) for k in self.key)))
        cur.execute(sql.SQL("CREATE TABLE {} ({})").format(self._target, sql.SQL(", ").join(defs)))
        self._created = True
        self.messages.append(f"table {self.schema_name}.{self.table_name} créée")

    def _open(self, table: pa.Table) -> None:
        self._conn = connect(self.connection)
        cur = self._conn.cursor()
        self._has_geom = has_geometry(table)
        try:
            if self._has_geom:
                self._srid = _epsg(self.crs or get_crs(table))
                if (self.crs or get_crs(table)) and not self._srid:
                    self.messages.append("SCR sans code EPSG : colonne créée avec SRID 0")
            exists = self._exists(cur)
            if exists and self.mode == "create":
                raise ConfigError(f"la table {self.schema_name}.{self.table_name} existe déjà (mode « create »)")
            if exists and self.mode == "overwrite":
                cur.execute(sql.SQL("DROP TABLE {}").format(self._target))
                exists = False
            if exists and self.mode == "truncate":
                cur.execute(sql.SQL("TRUNCATE {}").format(self._target))
            if not exists:
                self._create(cur, table)
            else:
                self._check_columns(cur, table)
        except Exception:
            self.abort()
            raise
        self._columns = [self.geometry_column if n == GEOM else n for n in table.column_names]

    def _check_columns(self, cur: psycopg.Cursor, table: pa.Table) -> None:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (self.schema_name, self.table_name),
        )
        existing = {r[0] for r in cur.fetchall()}
        wanted = [self.geometry_column if n == GEOM else n for n in table.column_names]
        absent = [c for c in wanted if c not in existing]
        if absent:
            raise ConfigError(
                f"colonnes absentes de la table {self.schema_name}.{self.table_name} : {', '.join(absent)}"
            )

    def _geometry_values(self, table: pa.Table) -> list[str | None]:
        geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
        if self._multi:
            single = {"MultiPolygon": 3, "MultiLineString": 1, "MultiPoint": 0}[self._multi]
            ctor = {3: shapely.MultiPolygon, 1: shapely.MultiLineString, 0: shapely.MultiPoint}[single]
            ids = shapely.get_type_id(geoms)
            for i in np.flatnonzero(ids == single):
                geoms[i] = ctor([geoms[i]])
        if self._srid:
            geoms = shapely.set_srid(geoms, self._srid)
        hexes = shapely.to_wkb(geoms, hex=True, include_srid=bool(self._srid))
        return [None if h is None else str(h) for h in hexes]

    def write(self, table: pa.Table) -> None:
        if self._conn is None:
            self._open(table)
        assert self._conn is not None
        names = [n for n in table.column_names]
        if [self.geometry_column if n == GEOM else n for n in names] != self._columns:
            raise ValueError("schéma incohérent entre deux lots")
        columns = []
        for n in names:
            columns.append(self._geometry_values(table) if n == GEOM else table.column(n).to_pylist())
        cols_sql = sql.SQL(", ").join(_ident(self.geometry_column if n == GEOM else n) for n in names)
        with self._conn.cursor() as cur:
            try:
                if self.mode == "upsert":
                    self._upsert(cur, cols_sql, columns)
                else:
                    with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(self._target, cols_sql)) as copy:
                        for row in zip(*columns, strict=True):
                            copy.write_row(row)
            except psycopg.errors.Error as exc:
                self.abort()
                raise ConfigError(f"écriture PostgreSQL : {_first_line(exc)}") from None
        self._rows += table.num_rows

    def _upsert(self, cur: psycopg.Cursor, cols_sql: sql.Composable, columns: list[list]) -> None:
        stage = sql.Identifier("_etl_stage")
        cur.execute(
            sql.SQL("CREATE TEMP TABLE IF NOT EXISTS {} (LIKE {} INCLUDING DEFAULTS)").format(stage, self._target)
        )
        cur.execute(sql.SQL("TRUNCATE {}").format(stage))
        with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(stage, cols_sql)) as copy:
            for row in zip(*columns, strict=True):
                copy.write_row(row)
        keys = sql.SQL(", ").join(_ident(k) for k in self.key)
        others = [c for c in (self._columns or []) if c not in self.key]
        action = (
            sql.SQL("DO UPDATE SET {}").format(
                sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=_ident(c)) for c in others)
            )
            if others
            else sql.SQL("DO NOTHING")
        )
        cur.execute(
            sql.SQL(
                "INSERT INTO {t} ({cols}) SELECT DISTINCT ON ({keys}) {cols} FROM {s} ORDER BY {keys}, ctid DESC "
                "ON CONFLICT ({keys}) {action}"
            ).format(t=self._target, cols=cols_sql, keys=keys, s=stage, action=action)
        )

    def abort(self) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
            finally:
                self._conn.close()
                self._conn = None

    def close(self) -> None:
        if self._conn is None:
            return
        conn, self._conn = self._conn, None
        try:
            with conn.cursor() as cur:
                if self._has_geom and self.create_spatial_index:
                    index = re.sub(r"\W", "_", f"{self.table_name}_{self.geometry_column}_gist")[:63]
                    cur.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING gist ({})").format(
                            _ident(index), self._target, _ident(self.geometry_column)
                        )
                    )
            conn.commit()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql.SQL("ANALYZE {}").format(self._target))
        except psycopg.errors.Error as exc:
            conn.rollback()
            raise ConfigError(f"écriture PostgreSQL : {_first_line(exc)}") from None
        finally:
            conn.close()


# ------------------------------------------------------------------------------- requêtes libres

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def bind_placeholders(statement: str) -> tuple[str, list[str]]:
    """`{{attribut}}` -> paramètre lié `%(attribut)s` (jamais collé dans le texte SQL : pas d'injection).

    Renvoie le SQL à passer à `execute(sql, valeurs)` et la liste des attributs référencés.
    """
    parts = PLACEHOLDER.split(statement.replace("%", "%%"))  # un « % » littéral (LIKE 'a%') est doublé
    names: list[str] = []
    out = []
    for i, part in enumerate(parts):
        if i % 2:  # nom capturé
            names.append(part)
            out.append(f"%({part})s")
        else:
            out.append(part)
    return "".join(out), list(dict.fromkeys(names))


def _convert(value: Any, oid: int, is_array: bool) -> Any:
    if value is None:
        return None
    if oid == 1700 and not is_array:
        return float(value)
    if oid in _SAME_AS_TEXT and not isinstance(value, str):
        return _as_text(value)
    if oid not in _SCALARS and not is_array:
        return value if isinstance(value, str) else str(value)
    return value


def result_table(cur: psycopg.Cursor, rows: list[tuple]) -> pa.Table:
    """Table Arrow des lignes d'un curseur (types d'après la description du résultat)."""
    desc = cur.description or []
    fields, arrays = [], []
    oids = sorted({d.type_code for d in desc})
    with cur.connection.cursor() as meta:
        meta.execute("SELECT oid, typcategory, typelem FROM pg_type WHERE oid = ANY(%s)", (oids,))
        kinds = {r[0]: (r[1] == "A", r[2]) for r in meta.fetchall()}
    for i, d in enumerate(desc):
        is_array, elem = kinds.get(d.type_code, (False, 0))
        base = _SCALARS.get(elem if is_array else d.type_code, pa.string())
        arrow = pa.list_(base) if is_array else base
        values = [_convert(r[i], elem if is_array else d.type_code, is_array) for r in rows]
        fields.append(pa.field(d.name, arrow))
        arrays.append(pa.array(values, type=arrow))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def run_statement(connection: str, statement: str, allow_write: bool = False) -> pa.Table | None:
    """Exécute un ordre SQL ; renvoie les lignes s'il en produit. Lecture seule sauf `allow_write`."""
    conn = connect(connection, read_only=not allow_write)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(statement)
            except psycopg.errors.Error as exc:
                raise ConfigError(f"SQL : {_first_line(exc)}") from None
            table = result_table(cur, cur.fetchall()) if cur.description else None
        if allow_write:
            conn.commit()
        return table
    finally:
        conn.close()
