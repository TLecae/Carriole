"""Bases de données hors PostgreSQL (Oracle) : lecture, écriture et SQL libre, communs à toutes.

Chaque base apporte un `Dialect` (voir `oracle.py`) : connexion, noms, types, géométrie (lue en WKB,
écrite depuis le WKB avec le SRID du flux), catalogue. Le reste est ici et se comporte comme `pg.py` :

- lecture en lecture seule, par lots ; la colonne géométrique devient `geometry` (WKB), son SRID le SCR du flux ;
- écriture dans une seule transaction (tout ou rien), modes create / overwrite / append / truncate / upsert ;
- SQL libre (DatabaseQuery) et SQL par entité (DatabaseQueryPerFeature) avec `{{attribut}}` passé en paramètre lié.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyproj
import shapely

from etl_engine import connections
from etl_engine.data import GEOM, get_crs, has_geometry, with_crs
from etl_engine.sdk import ConfigError

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class Column:
    name: str
    kind: str  # "geometry" | "text" (lue en texte) | "value" (type géré par le pilote) | "skip" (non lisible)


class Dialect:
    """Ce qui distingue une base : surchargé par chaque base."""

    kind = ""
    label = ""
    spatial = ""  # nom de l'extension spatiale (messages)
    null_geometry_literal = False  # vrai : une géométrie absente est écrite NULL dans le SQL (pas en paramètre)

    # -- connexion, erreurs
    def connect(self, info: connections.ConnectionInfo, read_only: bool) -> Any:
        raise NotImplementedError

    def errors(self) -> tuple[type[BaseException], ...]:
        raise NotImplementedError

    def server_info(self, conn: Any) -> dict[str, Any]:
        raise NotImplementedError

    # -- noms
    default_schema_sql = ""

    def quote(self, name: str) -> str:
        raise NotImplementedError

    def normalize(self, name: str) -> str:
        """Nom tel que la base le range (Oracle : majuscules pour un nom saisi sans guillemets)."""
        return name

    def table_ref(self, schema: str, table: str) -> str:
        return f"{self.quote(schema)}.{self.quote(table)}"

    def subquery(self, inner: str) -> str:
        return f"({inner}) _q"

    def empty(self, inner: str) -> str:
        return f"SELECT * FROM {self.subquery(inner)} WHERE 1 = 0"

    # -- lecture
    def describe(self, conn: Any, inner: str) -> list[Column]:
        raise NotImplementedError

    def geometry_select(self, column: str) -> str:
        raise NotImplementedError

    def text_select(self, column: str) -> str:
        raise NotImplementedError

    def geometry_text(self, column: str) -> str:
        """Une colonne géométrique autre que celle du flux : lue en WKT."""
        raise NotImplementedError

    def srid_sql(self, inner: str, column: str) -> str:
        raise NotImplementedError

    def fetch_arrow(self, conn: Any, statement: str, batch_rows: int) -> Iterator[pa.Table]:
        """Lots Arrow du résultat de `statement` ; au moins un (vide mais typé) si le résultat est vide."""
        raise NotImplementedError

    def arrow_type(self, description: Sequence[Any]) -> pa.DataType:
        """Type Arrow d'une colonne de résultat d'après sa description DB-API (résultat vide du SQL libre)."""
        return pa.string()

    def list_tables(self, conn: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- écriture
    def exists(self, cur: Any, schema: str, table: str) -> bool:
        raise NotImplementedError

    def table_columns(self, cur: Any, schema: str, table: str) -> list[str]:
        raise NotImplementedError

    def column_type(self, t: pa.DataType, key: bool) -> str:
        raise NotImplementedError

    def geometry_type(self) -> str:
        raise NotImplementedError

    def param(self, i: int) -> str:
        raise NotImplementedError

    def geometry_param(self, i: int, srid: int) -> str:
        raise NotImplementedError

    def truncate(self, target: str) -> str:
        return f"TRUNCATE TABLE {target}"

    def prepare_insert(self, cur: Any, kinds: list[str]) -> None:
        """Types des paramètres d'un INSERT (Oracle : les WKB en BLOB)."""

    def merge(self, target: str, columns: list[str], key: list[str], values: list[str]) -> str:
        raise NotImplementedError

    def after_create(
        self, cur: Any, schema: str, table: str, geometry_column: str, srid: int, bounds: list[float], has_key: bool
    ) -> list[str]:
        """Index spatial (et métadonnées) d'une table créée ; renvoie des messages."""
        return []

    def begin_read_only(self, conn: Any) -> None:
        """Lecture seule : par défaut rien, la transaction est annulée à la fermeture (rien n'est validé). Oracle :
        pas de SET TRANSACTION READ ONLY, qui refuse de lire une table créée à l'instant (ORA-01466)."""

    def placeholder(self, name: str) -> str:
        raise NotImplementedError

    @property
    def writer_class(self) -> type[DbWriter]:
        return DbWriter


def first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__


def epsg(crs: str | None) -> int:
    if not crs:
        return 0
    try:
        return pyproj.CRS.from_user_input(crs).to_epsg() or 0
    except pyproj.exceptions.CRSError:
        return 0


class Database:
    """Lecture, écriture et SQL d'une base, pour un dialecte."""

    def __init__(self, dialect: Dialect) -> None:
        self.d = dialect

    # ------------------------------------------------------------------------------------- connexion
    def connect(self, name: str, read_only: bool = False) -> Any:
        info = connections.resolve(name)
        if info.kind != self.d.kind:
            raise ConfigError(f"la connexion « {name} » n'est pas une connexion {self.d.label}")
        try:
            conn = self.d.connect(info, read_only)
        except self.d.errors() as exc:
            raise ConfigError(f"connexion « {name} » impossible : {first_line(exc)}") from None
        if read_only:
            self.d.begin_read_only(conn)
        return conn

    def check_connection(self, name: str) -> dict[str, Any]:
        conn = self.connect(name, read_only=True)
        try:
            return self.d.server_info(conn)
        finally:
            conn.close()

    def list_tables(self, name: str) -> list[dict[str, Any]]:
        conn = self.connect(name, read_only=True)
        try:
            return self.d.list_tables(conn)
        except self.d.errors() as exc:
            raise ConfigError(f"{self.d.label} : {first_line(exc)}") from None
        finally:
            conn.close()

    def split_table(self, conn: Any, name: str) -> tuple[str, str]:
        """« schéma.table » -> (schéma, table) ; sans schéma : celui de l'utilisateur."""
        parts = [p.strip() for p in name.split(".")]
        if not all(parts) or len(parts) > 2:
            raise ConfigError(f"nom de table invalide : « {name} » (attendu : schéma.table)")
        if len(parts) == 1:
            with conn.cursor() as cur:
                cur.execute(self.d.default_schema_sql)
                schema = str(cur.fetchone()[0])
            return schema, self._name(parts[0])
        return self._name(parts[0]), self._name(parts[1])

    def _name(self, raw: str) -> str:
        if len(raw) > 1 and raw[0] == raw[-1] == '"' or raw.startswith("["):
            return raw.strip('"[]')  # nom entre guillemets : tel quel
        return self.d.normalize(raw)

    # --------------------------------------------------------------------------------------- lecture
    def _inner(self, conn: Any, table: str, query: str) -> str:
        if query.strip():
            return query.strip().rstrip(";")
        schema, name = self.split_table(conn, table)
        return f"SELECT * FROM {self.d.table_ref(schema, name)}"

    def _plan(self, conn: Any, table: str, query: str, geometry_column: str, crs: str | None):
        inner = self._inner(conn, table, query)
        try:
            cols = self.d.describe(conn, inner)
        except self.d.errors() as exc:
            raise ConfigError(f"requête ou table invalide : {first_line(exc)}") from None
        geoms = [c for c in cols if c.kind == "geometry"]
        geom = None
        if geometry_column:
            geom = next((c for c in geoms if c.name.lower() == geometry_column.lower()), None)
            if geom is None:
                raise ConfigError(f"colonne géométrique « {geometry_column} » introuvable")
        elif geoms:
            geom = geoms[0]
        srid = 0
        if geom is not None and not crs:
            with conn.cursor() as cur:
                cur.execute(self.d.srid_sql(inner, geom.name))
                row = cur.fetchone()
            srid = int(row[0]) if row and row[0] else 0
        exprs = []
        for c in cols:
            q = self.d.quote(c.name)
            if c is geom:
                exprs.append(f"{self.d.geometry_select(q)} AS {self.d.quote(GEOM)}")
            elif c.kind == "geometry":
                exprs.append(f"{self.d.geometry_text(q)} AS {q}")
            elif c.kind == "text":
                exprs.append(f"{self.d.text_select(q)} AS {q}")
            elif c.kind == "value":
                exprs.append(q)
        statement = f"SELECT {', '.join(exprs)} FROM {self.d.subquery(inner)}"
        return statement, crs or (f"EPSG:{srid}" if srid else None), geom is not None

    @staticmethod
    def _normalize(table: pa.Table, crs: str | None, geometry: bool) -> pa.Table:
        """Types du pilote -> types du flux : décimaux en flottants, textes et binaires « larges » ramenés."""
        if isinstance(table, pa.RecordBatch):
            table = pa.Table.from_batches([table])
        arrays, fields = [], []
        for field, column in zip(table.schema, table.columns, strict=True):
            t = field.type
            if pa.types.is_decimal(t):
                try:  # entier (échelle nulle) qui tient sur 64 bits, sinon flottant
                    if t.scale != 0:
                        raise pa.ArrowInvalid("décimal")
                    column, t = pc.cast(column, pa.int64()), pa.int64()
                except pa.ArrowInvalid:
                    column, t = pc.cast(column, pa.float64()), pa.float64()
            elif pa.types.is_large_string(t):
                column, t = pc.cast(column, pa.string()), pa.string()
            elif (
                pa.types.is_large_binary(t)
                or pa.types.is_fixed_size_binary(t)
                or pa.types.is_null(t)
                and geometry
                and field.name == GEOM
            ):
                column, t = pc.cast(column, pa.binary()), pa.binary()
            arrays.append(column)
            fields.append(pa.field(field.name, t))
        out = pa.Table.from_arrays(arrays, schema=pa.schema(fields))
        return with_crs(out, crs) if geometry else out

    def read_schema(
        self, connection: str, table: str = "", query: str = "", geometry_column: str = "", crs: str | None = None, **_
    ) -> pa.Schema:
        conn = self.connect(connection, read_only=True)
        try:
            statement, crs, geometry = self._plan(conn, table, query, geometry_column, crs)
            first = next(iter(self.d.fetch_arrow(conn, self.d.empty(statement), 1)), None)
        except self.d.errors() as exc:
            raise ConfigError(f"requête ou table invalide : {first_line(exc)}") from None
        finally:
            conn.close()
        if first is None:
            return with_crs(pa.schema([]), crs)
        return self._normalize(first, crs, geometry).schema

    def read_batches(
        self,
        connection: str,
        batch_rows: int,
        table: str = "",
        query: str = "",
        where: str = "",
        geometry_column: str = "",
        crs: str | None = None,
        **_,
    ) -> Iterator[pa.Table]:
        conn = self.connect(connection, read_only=True)
        try:
            statement, crs, geometry = self._plan(conn, table, query, geometry_column, crs)
            if where.strip():
                statement = f"SELECT * FROM {self.d.subquery(statement)} WHERE ({where.strip()})"
            try:
                for batch in self.d.fetch_arrow(conn, statement, batch_rows):
                    if batch.num_rows:
                        yield self._normalize(batch, crs, geometry)
            except self.d.errors() as exc:
                raise ConfigError(f"lecture {self.d.label} : {first_line(exc)}") from None
        finally:
            conn.close()

    # ------------------------------------------------------------------------------------- SQL libre
    def bind_placeholders(self, statement: str) -> tuple[str, list[str]]:
        """`{{attribut}}` -> paramètre lié nommé (jamais collé dans le texte SQL : pas d'injection)."""
        names: list[str] = []
        out = []
        for i, part in enumerate(PLACEHOLDER.split(statement)):
            if i % 2:
                names.append(part)
                out.append(self.d.placeholder(part))
            else:
                out.append(part)
        return "".join(out), list(dict.fromkeys(names))

    def run_statement(self, connection: str, statement: str, allow_write: bool = False) -> pa.Table | None:
        """Exécute un ordre SQL ; renvoie les lignes s'il en produit. Sans `allow_write`, rien n'est validé."""
        session = self.session(connection, read_only=not allow_write)
        try:
            table = session.execute(statement.strip().rstrip(";"), {})
            if allow_write:
                session.commit()
            return table
        finally:
            session.close()

    def session(self, connection: str, read_only: bool) -> Session:
        return Session(self, self.connect(connection, read_only=read_only), read_only)

    def writer(self, connection: str, table: str, **options: Any) -> DbWriter:
        return self.d.writer_class(self, connection, table, **options)


class Session:
    """Connexion ouverte pour le SQL libre : une transaction, validée par `commit` (sinon annulée à la fermeture)."""

    def __init__(self, db: Database, conn: Any, read_only: bool) -> None:
        self.db, self.conn, self.read_only = db, conn, read_only

    def execute(self, statement: str, params: dict[str, Any]) -> pa.Table | None:
        d = self.db.d
        with self.conn.cursor() as cur:
            try:
                cur.execute(statement, params) if params else cur.execute(statement)
                if not cur.description:
                    return None
                names = [c[0] for c in cur.description]
                rows = cur.fetchall()
                if not rows:
                    return pa.schema([pa.field(c[0], d.arrow_type(c)) for c in cur.description]).empty_table()
            except d.errors() as exc:
                raise ConfigError(f"SQL : {first_line(exc)}") from None
        return rows_table(names, rows)

    def describe(self, statement: str, names: Sequence[str]) -> pa.Schema:
        table = self.execute(self.db.d.empty(statement), dict.fromkeys(names))
        return table.schema if table is not None else pa.schema([])

    def commit(self) -> None:
        if not self.read_only:
            self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


def flatten(table: pa.Table) -> pa.Table:
    """Colonnes écrivables telles quelles : listes, structures et dictionnaires en JSON texte, catégories en valeurs."""
    import json

    for i, field in enumerate(table.schema):
        t = field.type
        if pa.types.is_dictionary(t):
            table = table.set_column(i, field.name, table.column(i).cast(t.value_type))
        elif pa.types.is_nested(t):
            values = [
                None if v is None else json.dumps(v, ensure_ascii=False, default=str)
                for v in table.column(i).to_pylist()
            ]
            table = table.set_column(i, field.name, pa.array(values, pa.string()))
    return table


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**63 else float(value)
    if hasattr(value, "read"):  # LOB Oracle
        return value.read()
    return value


def rows_table(names: list[str], rows: list[Sequence[Any]]) -> pa.Table:
    """Table Arrow de lignes de résultat (types déduits des valeurs ; une colonne illisible part en texte)."""
    arrays = []
    for i in range(len(names)):
        values = [_plain(r[i]) for r in rows]
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays.append(pa.array([None if v is None else str(v) for v in values], pa.string()))
    return pa.Table.from_arrays(arrays, names=names) if names else pa.table({})


# ------------------------------------------------------------------------------------------- écriture


class DbWriter:
    """Écrit dans une seule transaction : tout ou rien. `abort()` annule, `close()` valide."""

    def __init__(
        self,
        db: Database,
        connection: str,
        table: str,
        mode: str = "append",
        key: list[str] | None = None,
        geometry_column: str = "geom",
        create_spatial_index: bool = True,
        crs: str | None = None,
        **_: Any,
    ) -> None:
        if mode == "upsert" and not key:
            raise ConfigError("le mode « upsert » demande au moins une colonne clé")
        self.db, self.d = db, db.d
        self.connection, self.table, self.mode, self.key = connection, table, mode, list(key or [])
        self.geometry_column, self.create_spatial_index, self.crs = geometry_column, create_spatial_index, crs
        self.messages: list[str] = []
        self._conn: Any = None
        self._info: connections.ConnectionInfo | None = None
        self._target = ""
        self._schema = self._name = ""
        self._columns: list[str] = []  # colonnes de la table, dans l'ordre des colonnes du flux
        self._created = False
        self._srid = 0
        self._bounds: list[float] | None = None
        self._rows = 0

    def _open(self, table: pa.Table) -> None:
        self._conn = self.db.connect(self.connection)
        self._schema, self._name = self.db.split_table(self._conn, self.table)
        self._target = self.d.table_ref(self._schema, self._name)
        geometry = has_geometry(table)
        if geometry:
            source = self.crs or get_crs(table)
            self._srid = epsg(source)
            if source and not self._srid:
                self.messages.append("SCR sans code EPSG : géométries écrites avec le SRID 0")
        cur = self._conn.cursor()
        try:
            exists = self.d.exists(cur, self._schema, self._name)
            if exists and self.mode == "create":
                raise ConfigError(f"la table {self._schema}.{self._name} existe déjà (mode « create »)")
            if exists and self.mode == "overwrite":
                cur.execute(f"DROP TABLE {self._target}")
                exists = False
            if exists and self.mode == "truncate":
                cur.execute(self.d.truncate(self._target))
            if exists:
                self._match_columns(cur, table)
            else:
                self._create(cur, table)
        except self.d.errors() as exc:
            self.abort()
            raise ConfigError(f"écriture {self.d.label} : {first_line(exc)}") from None
        except Exception:
            self.abort()
            raise
        finally:
            cur.close()

    def _create(self, cur: Any, table: pa.Table) -> None:
        defs, self._columns = [], []
        for field in table.schema:
            if field.name == GEOM:
                name = self.d.normalize(self.geometry_column)
                defs.append(f"{self.d.quote(name)} {self.d.geometry_type()}")
            else:
                name = self.d.normalize(field.name)
                defs.append(f"{self.d.quote(name)} {self.d.column_type(field.type, field.name in self.key)}")
            self._columns.append(name)
        if self.key:
            missing = [k for k in self.key if k not in table.column_names]
            if missing:
                raise ConfigError(f"colonne clé absente des données : {', '.join(missing)}")
            keys = ", ".join(self.d.quote(self.d.normalize(k)) for k in self.key)
            defs.append(f"PRIMARY KEY ({keys})")
        cur.execute(f"CREATE TABLE {self._target} ({', '.join(defs)})")
        self._created = True
        self.messages.append(f"table {self._schema}.{self._name} créée")

    def _match_columns(self, cur: Any, table: pa.Table) -> None:
        existing = {c.lower(): c for c in self.d.table_columns(cur, self._schema, self._name)}
        wanted = [self.geometry_column if n == GEOM else n for n in table.column_names]
        absent = [c for c in wanted if c.lower() not in existing]
        if absent:
            raise ConfigError(f"colonnes absentes de la table {self._schema}.{self._name} : {', '.join(absent)}")
        self._columns = [existing[c.lower()] for c in wanted]

    def _track(self, table: pa.Table) -> None:
        """Emprise des géométries écrites (index spatial créé à la fin)."""
        if GEOM not in table.column_names:
            return
        geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
        present = geoms[~shapely.is_missing(geoms)]
        if not len(present):
            return
        b = [float(v) for v in shapely.total_bounds(present)]
        old = self._bounds or b
        self._bounds = [min(old[0], b[0]), min(old[1], b[1]), max(old[2], b[2]), max(old[3], b[3])]

    def _values(self, table: pa.Table) -> tuple[list[str], list[tuple]]:
        self._track(table)
        table = flatten(table)
        kinds, columns = [], []
        for name in table.column_names:
            if name == GEOM:
                kinds.append("geometry")
                columns.append([None if w is None else bytes(w) for w in table.column(GEOM).to_pylist()])
            else:
                kinds.append("value")
                columns.append(table.column(name).to_pylist())
        return kinds, list(zip(*columns, strict=True))

    def write(self, table: pa.Table) -> None:
        if self._conn is None:
            self._open(table)
        if len(self._columns) != table.num_columns:
            raise ValueError("schéma incohérent entre deux lots")
        kinds, rows = self._values(table)
        groups = [(kinds, rows)]
        if "geometry" in kinds and self.d.null_geometry_literal:  # géométrie absente : NULL écrit dans le SQL
            g = kinds.index("geometry")
            groups = [
                (kinds, [r for r in rows if r[g] is not None]),
                ([*kinds[:g], "null", *kinds[g + 1 :]], [(*r[:g], *r[g + 1 :]) for r in rows if r[g] is None]),
            ]
        with self._conn.cursor() as cur:
            for group_kinds, group_rows in groups:
                if not group_rows:
                    continue
                try:
                    self.d.prepare_insert(cur, [k for k in group_kinds if k != "null"])
                    cur.executemany(self._statement(table, group_kinds), group_rows)
                except self.d.errors() as exc:
                    self.abort()
                    raise ConfigError(f"écriture {self.d.label} : {first_line(exc)}") from None
        self._rows += table.num_rows

    def _statement(self, table: pa.Table, kinds: list[str]) -> str:
        """INSERT (ou MERGE en upsert) d'un lot ; `kinds` : « value », « geometry » ou « null » par colonne."""
        placeholders, bound = [], 0
        for k in kinds:
            if k == "null":
                placeholders.append("NULL")
                continue
            placeholders.append(self.d.geometry_param(bound, self._srid) if k == "geometry" else self.d.param(bound))
            bound += 1
        cols = [self.d.quote(c) for c in self._columns]
        if self.mode == "upsert":
            keys = [self.d.quote(self._columns[table.column_names.index(k)]) for k in self.key]
            return self.d.merge(self._target, cols, keys, placeholders)
        return f"INSERT INTO {self._target} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"

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
            if self._created and self.create_spatial_index and self.geometry_column and self._bounds is not None:
                with conn.cursor() as cur:
                    geometry = self.d.normalize(self.geometry_column)
                    self.messages += self.d.after_create(
                        cur, self._schema, self._name, geometry, self._srid, self._bounds, bool(self.key)
                    )
            conn.commit()
        except self.d.errors() as exc:
            conn.rollback()
            raise ConfigError(f"écriture {self.d.label} : {first_line(exc)}") from None
        finally:
            conn.close()
