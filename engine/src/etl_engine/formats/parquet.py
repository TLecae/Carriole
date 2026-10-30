"""GeoParquet (format natif des échanges entre nœuds)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyproj
import shapely

from etl_engine import duck
from etl_engine.atomic import part_name
from etl_engine.data import GEOM, get_crs, has_geometry, with_crs
from etl_engine.sdk import ConfigError

_TYPE_NAMES = {
    0: "Point",
    1: "LineString",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}


def _crs_from_geo(geo: dict[str, Any] | None, column: str) -> str | None:
    if not geo:
        return None
    col = geo.get("columns", {}).get(column, {})
    projjson = col.get("crs")
    if projjson is None:
        return "OGC:CRS84"  # défaut de la spécification GeoParquet
    try:
        return pyproj.CRS.from_json_dict(projjson).to_string()
    except pyproj.exceptions.CRSError:
        return None


def _prepare(schema: pa.Schema, metadata: dict[bytes, bytes]) -> tuple[pa.Schema, str | None, str | None]:
    """(schéma interne, colonne géométrie d'origine, SCR) pour un fichier Parquet."""
    geo = json.loads(metadata[b"geo"]) if b"geo" in metadata else None
    column = geo["primary_column"] if geo else (GEOM if GEOM in schema.names else None)
    crs = get_crs(schema) or _crs_from_geo(geo, column) if column else None
    if column and column != GEOM:
        schema = schema.set(schema.get_field_index(column), pa.field(GEOM, schema.field(column).type))
    return with_crs(schema.remove_metadata(), crs), column, crs


def is_remote(path: str) -> bool:
    """Adresse web (http, https) : le fichier est lu par plages, par DuckDB, sans être téléchargé en entier."""
    return path.lower().startswith(("http://", "https://"))


_web_schemas: dict[str, tuple[float, pa.Schema, dict[bytes, bytes]]] = {}
_web_failures: dict[str, tuple[float, str]] = {}
WEB_SCHEMA_SECONDS = 60.0  # l'éditeur redemande le schéma à chaque modification du flux : pas une requête à chaque fois


@contextlib.contextmanager
def _web(url: str) -> Iterator[duckdb.DuckDBPyConnection]:
    con = duck.connect("expr")
    try:
        con.execute(f"SET threads = {min(4, duck.machine_threads())}")  # plusieurs plages demandées à la fois
        duck.load_httpfs(con)
        con.execute("SET enable_geoparquet_conversion = false")  # géométrie en WKB, comme un fichier local
        yield con
    except duckdb.Error as exc:
        raise ConfigError(f"lecture impossible : {url} : {str(exc).splitlines()[0]}") from None
    finally:
        con.close()


def _web_footer(con: duckdb.DuckDBPyConnection, url: str) -> tuple[pa.Schema, dict[bytes, bytes]]:
    """Schéma Arrow et métadonnées (« geo ») d'un fichier web, lus dans son pied de page."""
    known = _web_schemas.get(url)
    if known and time.monotonic() - known[0] < WEB_SCHEMA_SECONDS:
        return known[1], known[2]
    metadata = {
        bytes(k): bytes(v) for k, v in con.execute("SELECT key, value FROM parquet_kv_metadata(?)", [url]).fetchall()
    }
    schema = con.sql("SELECT * FROM read_parquet(?) LIMIT 0", params=[url]).to_arrow_table().schema
    _web_schemas[url] = (time.monotonic(), schema, metadata)
    return schema, metadata


def read_schema(path: str, **_: Any) -> pa.Schema:
    if is_remote(path):
        failed = _web_failures.get(path)
        if failed and time.monotonic() - failed[0] < WEB_SCHEMA_SECONDS:
            raise ConfigError(failed[1])  # adresse injoignable : l'éditeur n'attend pas le réseau à chaque modification
        try:
            with _web(path) as con:
                schema, metadata = _web_footer(con, path)
        except ConfigError as exc:
            _web_failures[path] = (time.monotonic(), str(exc))
            raise
    else:
        pf = pq.ParquetFile(path)
        schema, metadata = pf.schema_arrow, pf.metadata.metadata or {}
    return _prepare(schema, metadata)[0]


def read_batches(
    path: str, batch_rows: int, columns: list[str] | None = None, limit: int | None = None, **_: Any
) -> Iterator[pa.Table]:
    """Lots du fichier ; `limit` : au plus autant de lignes (une adresse web n'en télécharge pas plus)."""
    if is_remote(path):
        yield from _read_web(path, batch_rows, columns, limit)
        return
    pf = pq.ParquetFile(path)
    _, column, crs = _prepare(pf.schema_arrow, pf.metadata.metadata or {})
    for rb in pf.iter_batches(batch_size=batch_rows, columns=_wanted(columns, column)):
        yield _named(pa.Table.from_batches([rb]), column, crs)


def _wanted(columns: list[str] | None, column: str | None) -> list[str] | None:
    """Colonnes à lire, la géométrie sous son nom dans le fichier."""
    return None if columns is None else [column if (c == GEOM and column) else c for c in columns]


def _named(table: pa.Table, column: str | None, crs: str | None) -> pa.Table:
    if column and column != GEOM:
        table = table.rename_columns([GEOM if n == column else n for n in table.column_names])
    if GEOM in table.column_names:  # sans l'extension « geoarrow.wkb » que DuckDB pose : comme un fichier local
        index = table.column_names.index(GEOM)
        table = table.set_column(index, table.field(index).remove_metadata(), table.column(index))
    return with_crs(table, crs)


def _read_web(url: str, batch_rows: int, columns: list[str] | None, limit: int | None) -> Iterator[pa.Table]:
    with _web(url) as con:
        schema, metadata = _web_footer(con, url)
        _, column, crs = _prepare(schema, metadata)
        wanted = _wanted(columns, column)
        select = "*" if wanted is None else ", ".join('"' + c.replace('"', '""') + '"' for c in wanted)
        bound = ""
        if limit:  # la lecture anticipée de DuckDB 2 téléchargerait la plupart des groupes de lignes pour 10 lignes
            con.execute("SET disable_parquet_prefetching = true")
            bound = f" LIMIT {int(limit)}"
        with con.sql(f"SELECT {select} FROM read_parquet(?){bound}", params=[url]).to_arrow_reader(
            batch_rows
        ) as reader:
            for rb in reader:
                yield _named(pa.Table.from_batches([rb]), column, crs)


class ParquetWriter:
    """Écrit un GeoParquet 1.1 par lots (WKB, SCR, emprise et types de géométrie dans les métadonnées)."""

    def __init__(self, path: str, compression: str = "zstd", **_: Any) -> None:
        self.path = path
        self._part = part_name(path)  # tout ou rien : le fichier n'apparaît qu'à la fermeture
        self.compression = compression
        self._writer: pq.ParquetWriter | None = None
        self._schema: pa.Schema | None = None
        self._bounds: np.ndarray | None = None
        self._types: set[str] = set()
        self._crs: str | None = None

    def _track(self, table: pa.Table) -> None:
        geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
        b = shapely.bounds(geoms)
        if len(b) and not np.isnan(b).all():
            lo, hi = np.nanmin(b[:, :2], axis=0), np.nanmax(b[:, 2:], axis=0)
            cur = self._bounds
            self._bounds = (
                np.concatenate([lo, hi])
                if cur is None
                else np.concatenate([np.minimum(cur[:2], lo), np.maximum(cur[2:], hi)])
            )
        ids = shapely.get_type_id(geoms)
        z = shapely.has_z(geoms)
        for tid, has_z in {(int(i), bool(zz)) for i, zz in zip(ids, z, strict=True) if i >= 0}:
            self._types.add(_TYPE_NAMES.get(tid, "Geometry") + (" Z" if has_z else ""))

    def write(self, table: pa.Table) -> None:
        if self._writer is None:
            self._schema = table.schema
            self._crs = get_crs(table)
            self._writer = pq.ParquetWriter(self._part, self._schema, compression=self.compression)
        elif not table.schema.equals(self._schema, check_metadata=False):
            raise ValueError("schéma incohérent entre deux lots")
        if has_geometry(table):
            self._track(table)
        self._writer.write_table(table.replace_schema_metadata(self._schema.metadata))

    def close(self) -> None:
        if self._writer is None:
            return
        if has_geometry(self._schema):
            col: dict[str, Any] = {"encoding": "WKB", "geometry_types": sorted(self._types)}
            if self._crs:
                with contextlib.suppress(pyproj.exceptions.CRSError):
                    col["crs"] = pyproj.CRS.from_user_input(self._crs).to_json_dict()
            if self._bounds is not None:
                col["bbox"] = [float(x) for x in self._bounds]
            geo = {"version": "1.1.0", "primary_column": GEOM, "columns": {GEOM: col}}
            self._writer.add_key_value_metadata({"geo": json.dumps(geo)})
        self._writer.close()
        self._writer = None
        os.replace(self._part, self.path)

    def abort(self) -> None:
        """Écriture interrompue : le fichier de destination n'est pas touché."""
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
            self._writer = None
            with contextlib.suppress(OSError):
                os.unlink(self._part)


MAX_PARTITIONS = 500


class PartitionedParquetWriter:
    """GeoParquet partitionné : un dossier `colonne=valeur/` par valeur, la colonne reste dans les fichiers
    (le dossier se relit avec un motif `dossier/**/*.parquet`)."""

    messages: list[str]

    def __init__(
        self, path: str, partition_by: list[str], compression: str = "zstd", mode: str = "overwrite", **_: Any
    ):
        if not partition_by:
            raise ValueError("partition_by vide")
        self.path, self.by, self.compression = path, list(partition_by), compression
        self._writers: dict[tuple, ParquetWriter] = {}
        self.messages = []
        self._started = False
        self._mode = mode

    def _dir(self, key: tuple) -> str:
        parts = []
        for col, value in zip(self.by, key, strict=True):
            text = "__null__" if value is None else re.sub(r"[^\w.\- ]", "_", str(value))[:100]
            parts.append(f"{col}={text}")
        return os.path.join(self.path, *parts)

    def write(self, table: pa.Table) -> None:
        missing = [c for c in self.by if c not in table.column_names]
        if missing:
            raise ValueError(f"colonne de partition absente : {', '.join(missing)}")
        if not self._started:
            if self._mode == "overwrite" and os.path.isdir(self.path):
                for root, _, files in os.walk(self.path):
                    for name in files:
                        if name.endswith(".parquet"):
                            os.remove(os.path.join(root, name))
            self._started = True
        keys = list(zip(*[table.column(c).to_pylist() for c in self.by], strict=True))
        groups: dict[tuple, list[int]] = defaultdict(list)
        for i, key in enumerate(keys):
            groups[key].append(i)
        for key, rows in groups.items():
            writer = self._writers.get(key)
            if writer is None:
                if len(self._writers) >= MAX_PARTITIONS:
                    raise ValueError(f"plus de {MAX_PARTITIONS} partitions : choisir une colonne moins fine")
                folder = self._dir(key)
                os.makedirs(folder, exist_ok=True)
                writer = self._writers[key] = ParquetWriter(os.path.join(folder, "part-0.parquet"), self.compression)
            writer.write(table.take(pa.array(rows)))

    def abort(self) -> None:
        for writer in self._writers.values():
            writer.abort()

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()
        self.messages.append(f"{len(self._writers)} partition(s) écrite(s) dans {self.path}")
