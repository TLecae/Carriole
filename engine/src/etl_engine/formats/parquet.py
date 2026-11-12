"""GeoParquet (format natif des échanges entre nœuds)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyproj
import shapely

from etl_engine import duck
from etl_engine.atomic import part_name
from etl_engine.cache import batch_rows_for, dictionary_columns
from etl_engine.data import GEOM, crs_from_geo, get_crs, has_geometry, plain_geometry, with_crs
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


# Encodages GeoArrow natifs de GeoParquet 1.1 : coordonnées en listes imbriquées au lieu du WKB (type de géométrie,
# profondeur des listes au-dessus des coordonnées)
_NATIVE = {
    "point": (shapely.GeometryType.POINT, 0),
    "linestring": (shapely.GeometryType.LINESTRING, 1),
    "multipoint": (shapely.GeometryType.MULTIPOINT, 1),
    "polygon": (shapely.GeometryType.POLYGON, 2),
    "multilinestring": (shapely.GeometryType.MULTILINESTRING, 2),
    "multipolygon": (shapely.GeometryType.MULTIPOLYGON, 3),
}


def _native(metadata: dict[bytes, bytes], column: str | None) -> str | None:
    """Encodage GeoArrow natif de la colonne géométrie (« multipolygon »…) ; None pour du WKB."""
    if not column or b"geo" not in metadata:
        return None
    encoding = str(json.loads(metadata[b"geo"]).get("columns", {}).get(column, {}).get("encoding", "")).lower()
    return encoding if encoding in _NATIVE else None


def _prepare(schema: pa.Schema, metadata: dict[bytes, bytes]) -> tuple[pa.Schema, str | None, str | None]:
    """(schéma interne, colonne géométrie d'origine, SCR) pour un fichier Parquet."""
    geo = json.loads(metadata[b"geo"]) if b"geo" in metadata else None
    column = geo["primary_column"] if geo else (GEOM if GEOM in schema.names else None)
    crs = get_crs(schema) or crs_from_geo(geo, column) if column else None
    if column:  # nommée `geometry`, sans métadonnées de champ (extension « geoarrow.wkb » : voir `plain_geometry`)
        kind = pa.binary() if _native(metadata, column) else schema.field(column).type  # lue en WKB : `_as_wkb`
        schema = schema.set(schema.get_field_index(column), pa.field(GEOM, kind))
    return with_crs(schema.remove_metadata(), crs), column, crs


def _coords(level: pa.Array) -> np.ndarray:
    """Coordonnées (n, 2 ou 3) : struct x, y[, z][, m] (« separated ») ou liste fixe (« interleaved ») ; M ignorée."""
    if pa.types.is_struct(level.type):
        names = [level.type.field(i).name for i in range(level.type.num_fields)]
        fields = level.flatten()  # avec le décalage du tableau ; une entité nulle donne NaN
        dims = [fields[names.index(d)].to_numpy(zero_copy_only=False) for d in ("x", "y", "z") if d in names]
        return np.column_stack(dims).astype(np.float64, copy=False)
    size = level.type.list_size
    dims = 2 if size == 2 or (size == 3 and level.type.value_field.name == "xym") else 3  # champ nommé xy, xyz, xym…
    values = level.values.slice(level.offset * size, len(level) * size)  # `flatten` sauterait les entités nulles
    return np.ascontiguousarray(values.to_numpy(zero_copy_only=False).reshape(-1, size)[:, :dims], np.float64)


def _as_wkb(table: pa.Table, encoding: str | None) -> pa.Table:
    """Géométrie GeoArrow native réécrite en WKB, d'un coup par bloc (`shapely.from_ragged_array`)."""
    if not encoding or GEOM not in table.column_names:
        return table
    kind, depth = _NATIVE[encoding]
    chunks = []
    for chunk in table.column(GEOM).chunks:
        level, offsets = chunk, []
        for _ in range(depth):  # du multipolygone vers les anneaux : décalages ramenés au début du bloc
            o = level.offsets.to_numpy().astype(np.int64)
            offsets.append(o - o[0])
            level = level.values.slice(int(o[0]), int(o[-1] - o[0]))
        geoms = shapely.from_ragged_array(kind, _coords(level), tuple(reversed(offsets)) or None)
        geoms[chunk.is_null().to_numpy(zero_copy_only=False)] = None
        chunks.append(pa.array(shapely.to_wkb(geoms), pa.binary()))
    column = pa.chunked_array(chunks, pa.binary())
    return table.set_column(table.schema.get_field_index(GEOM), pa.field(GEOM, pa.binary()), column)


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
    metadata = pf.metadata.metadata or {}
    _, column, crs = _prepare(pf.schema_arrow, metadata)
    native = _native(metadata, column)
    rows = batch_rows_for([Path(path)], batch_rows)  # lignes larges : des lots plus courts, dans le budget du run
    for rb in pf.iter_batches(batch_size=rows, columns=_wanted(columns, column)):
        yield _named(pa.Table.from_batches([rb]), column, crs, native)


def _wanted(columns: list[str] | None, column: str | None) -> list[str] | None:
    """Colonnes à lire, la géométrie sous son nom dans le fichier."""
    return None if columns is None else [column if (c == GEOM and column) else c for c in columns]


def _named(table: pa.Table, column: str | None, crs: str | None, native: str | None = None) -> pa.Table:
    if column and column != GEOM:
        table = table.rename_columns([GEOM if n == column else n for n in table.column_names])
    table = _as_wkb(table, native)
    return with_crs(plain_geometry(table), crs)  # sans l'extension « geoarrow.wkb » : comme un fichier local


def _read_web(url: str, batch_rows: int, columns: list[str] | None, limit: int | None) -> Iterator[pa.Table]:
    with _web(url) as con:
        schema, metadata = _web_footer(con, url)
        _, column, crs = _prepare(schema, metadata)
        native = _native(metadata, column)
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
                yield _named(pa.Table.from_batches([rb]), column, crs, native)


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _footer(path: str) -> tuple[pa.Schema, dict[bytes, bytes]]:
    if is_remote(path):
        with _web(path) as con:
            return _web_footer(con, path)
    pf = pq.ParquetFile(path)
    return pf.schema_arrow, pf.metadata.metadata or {}


def sql_query(paths: list[str], add_filename: bool = False, limit: int | None = None, **_: Any) -> str | None:
    """Requête DuckDB qui lit `paths` comme `read_batches` : géométrie (WKB) renommée `geometry`, nom du fichier,
    `limit` premières lignes. None (lecture par `read_batches`) si les fichiers n'ont pas la même colonne géométrie,
    ou une géométrie GeoArrow native (que `read_batches` réécrit en WKB)."""
    footers = [_footer(path) for path in paths]
    columns = [_prepare(*footer)[1] for footer in footers]
    found = set(columns)
    names = footers[0][0].names
    column = found.pop() if len(found) == 1 else ""
    if column == "" or (column and column != GEOM and GEOM in names):
        return None
    if any(_native(metadata, c) for (_, metadata), c in zip(footers, columns, strict=True)):
        return None
    listing = ", ".join(sql_literal(p.replace("\\", "/")) for p in paths)
    filename = add_filename and "filename" not in names
    query = f"SELECT * FROM read_parquet([{listing}], union_by_name=true, filename={filename})"
    if filename:  # le nom seul, comme `read_batches`
        query = f"SELECT * REPLACE (parse_filename(filename, false, 'both_slash') AS filename) FROM ({query})"
    if column and column != GEOM:
        query = f"SELECT * RENAME ({sql_name(column)} AS {GEOM}) FROM ({query})"
    return f"{query} LIMIT {int(limit)}" if limit else query


def as_port(paths: list[str], add_filename: bool = False, limit: int | None = None, **_: Any) -> str | None:
    """Le fichier, s'il se lit tel quel comme un port : un seul fichier local, sans colonne ajoutée ni limite, sans
    géométrie ou avec sa géométrie en WKB dans `geometry`, SCR lu de même par `get_crs`. Le port le désigne alors
    au lieu d'en recopier les lignes (ADR 0043). None sinon."""
    if len(paths) != 1 or is_remote(paths[0]) or add_filename or limit:
        return None
    schema, metadata = _footer(paths[0])
    _, column, crs = _prepare(schema, metadata)
    if column is None:
        return paths[0]
    kind = schema.field(GEOM).type if GEOM in schema.names else None
    wkb = kind is not None and (pa.types.is_binary(kind) or pa.types.is_large_binary(kind))
    return paths[0] if column == GEOM and wkb and get_crs(schema) == crs else None


def sql_prepare(con: duckdb.DuckDBPyConnection, paths: list[str], limit: int | None = None) -> None:
    """Connexion de `sql_query` : géométrie lue en WKB ; extension web et lecture anticipée comme `_read_web`."""
    con.execute("SET enable_geoparquet_conversion = false")
    if any(is_remote(p) for p in paths):
        duck.load_httpfs(con)
        if limit:
            con.execute("SET disable_parquet_prefetching = true")


def file_columns(path: str) -> list[dict[str, str]] | None:
    """Colonnes d'un fichier Parquet existant, la géométrie nommée `geometry` (voir `_prepare`) ; None si ce n'est pas
    un fichier (absent, ou dossier partitionné)."""
    if not os.path.isfile(path):
        return None
    try:
        schema = read_schema(path)
    except Exception:  # noqa: BLE001 - fichier illisible : l'écriture dira pourquoi
        return None
    return [{"name": f.name, "type": str(f.type)} for f in schema]


def _same_crs(a: str, b: str) -> bool:
    try:
        return pyproj.CRS.from_user_input(a) == pyproj.CRS.from_user_input(b)
    except pyproj.exceptions.CRSError:
        return a == b


class ParquetWriter:
    """Écrit un GeoParquet 1.1 par lots (WKB, SCR, emprise et types de géométrie dans les métadonnées). Un fichier
    Parquet ne se complète pas sur place : l'ajout réécrit ses lignes puis les nouvelles, rangées selon ses colonnes."""

    def __init__(self, path: str, compression: str = "zstd", mode: str = "overwrite", **_: Any) -> None:
        if mode == "create" and os.path.exists(path):
            raise ConfigError(f"le fichier existe déjà : {path}")
        self.path = path
        self._part = part_name(path)  # tout ou rien : le fichier n'apparaît qu'à la fermeture
        self.compression = compression
        self._append = mode == "append" and os.path.isfile(path)
        self._target: pa.Schema | None = None  # ajout : colonnes du fichier existant
        self._writer: pq.ParquetWriter | None = None
        self._schema: pa.Schema | None = None
        self._bounds: np.ndarray | None = None
        self._types: set[str] = set()
        self._crs: str | None = None

    def _aligned(self, table: pa.Table) -> pa.Table:
        """Ajout : les colonnes du fichier, dans son ordre et ses types ; vides pour celles que rien n'alimente."""
        target = self._target
        assert target is not None
        absent = [n for n in table.column_names if n not in target.names]
        if absent:
            listing = ", ".join(absent)
            raise ConfigError(f"attributs absents du fichier : {listing}")
        arrays = []
        for field in target:
            if field.name not in table.column_names:
                arrays.append(pa.nulls(table.num_rows, field.type))
                continue
            column = table.column(field.name)
            if column.type != field.type:
                try:
                    column = column.cast(field.type)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    name, kind, wanted = field.name, column.type, field.type
                    raise ConfigError(
                        f"attribut « {name} » : {kind} ne s'écrit pas dans une colonne {wanted}"
                    ) from None
            arrays.append(column)
        return pa.Table.from_arrays(arrays, schema=pa.schema(list(target), metadata=table.schema.metadata))

    def _existing(self, table: pa.Table) -> tuple[pa.Table, pq.ParquetFile]:
        """Ajout : le fichier existant, et le premier lot rangé selon ses colonnes (SCR du fichier, s'il en a un)."""
        existing = pq.ParquetFile(self.path)
        self._target, _, crs = _prepare(existing.schema_arrow, existing.metadata.metadata or {})
        incoming = get_crs(table)
        if crs and incoming and not _same_crs(crs, incoming):
            raise ConfigError(f"système de coordonnées différent de celui du fichier : {incoming} (fichier : {crs})")
        self._crs = crs or incoming
        return self._aligned(table), existing

    def _copy(self, existing: pq.ParquetFile) -> None:
        """Ajout : les lignes du fichier existant, groupe par groupe, avant les nouvelles."""
        assert self._writer is not None and self._schema is not None
        metadata = existing.metadata.metadata or {}
        _, column, _ = _prepare(existing.schema_arrow, metadata)
        native = _native(metadata, column)  # géométrie native : le fichier complété l'aura en WKB
        for i in range(existing.num_row_groups):
            group = existing.read_row_group(i)
            if column and column != GEOM:
                group = group.rename_columns([GEOM if n == column else n for n in group.column_names])
            group = pa.Table.from_arrays(_as_wkb(group, native).columns, schema=self._schema)
            if has_geometry(self._schema):
                self._track(group)
            self._writer.write_table(group)

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
        present = ids >= 0
        # types présents, sans boucle sur les entités : (type, a un Z) codé en un entier
        for code in np.unique(ids[present] * 2 + shapely.has_z(geoms[present])):
            tid, has_z = divmod(int(code), 2)
            self._types.add(_TYPE_NAMES.get(tid, "Geometry") + (" Z" if has_z else ""))

    def _geo(self, final: bool) -> str:
        """Métadonnées GeoParquet. Celles de l'ouverture (`final` faux : SCR et encodage, types inconnus) vont dans le
        schéma Arrow que le fichier recopie ; les lecteurs qui passent par Arrow (GDAL, donc QGIS, geopandas…) ne
        voient que celles-là. Celles de la fermeture (types et emprise en plus) remplacent `geo` du pied du fichier."""
        col: dict[str, Any] = {"encoding": "WKB", "geometry_types": sorted(self._types) if final else []}
        if self._crs:
            with contextlib.suppress(pyproj.exceptions.CRSError):
                col["crs"] = pyproj.CRS.from_user_input(self._crs).to_json_dict()
        if final and self._bounds is not None:
            col["bbox"] = [float(x) for x in self._bounds]
        return json.dumps({"version": "1.1.0", "primary_column": GEOM, "columns": {GEOM: col}})

    def write(self, table: pa.Table) -> None:
        if self._writer is None:
            existing = None
            if self._append:
                table, existing = self._existing(table)
            else:
                self._crs = get_crs(table)
            self._schema = table.schema
            if has_geometry(table):
                self._schema = table.schema.with_metadata({**(table.schema.metadata or {}), b"geo": self._geo(False)})
            self._writer = pq.ParquetWriter(
                self._part, self._schema, compression=self.compression, use_dictionary=dictionary_columns(self._schema)
            )
            if existing is not None:
                self._copy(existing)
        elif self._target is not None:
            table = self._aligned(table)
        elif not table.schema.equals(self._schema, check_metadata=False):
            raise ValueError("schéma incohérent entre deux lots")
        if has_geometry(table):
            self._track(table)
        self._writer.write_table(table.replace_schema_metadata(self._schema.metadata))

    def close(self) -> None:
        if self._writer is None:
            return
        if has_geometry(self._schema):
            self._writer.add_key_value_metadata({"geo": self._geo(True)})
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
        if mode == "create" and os.path.exists(path):
            raise ConfigError(f"le dossier existe déjà : {path}")
        self.path, self.by, self.compression = path, list(partition_by), compression
        # ajout : un fichier de plus par partition, à côté de ceux des runs précédents
        self._name = f"part-{uuid.uuid4().hex[:8]}.parquet" if mode == "append" else "part-0.parquet"
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
        # lignes de chaque valeur, sans boucle sur les entités (ordre des lignes gardé : un seul thread)
        rows_of = pa.array(np.arange(table.num_rows, dtype=np.int64))
        grouped = table.select(self.by).append_column("__row", rows_of).group_by(self.by, use_threads=False)
        grouped = grouped.aggregate([("__row", "list")])
        keys = list(zip(*[grouped.column(c).to_pylist() for c in self.by], strict=True))
        for key, rows in zip(keys, grouped.column("__row_list").combine_chunks(), strict=True):
            rows = rows.values
            writer = self._writers.get(key)
            if writer is None:
                if len(self._writers) >= MAX_PARTITIONS:
                    raise ValueError(f"plus de {MAX_PARTITIONS} partitions : choisir une colonne moins fine")
                folder = self._dir(key)
                os.makedirs(folder, exist_ok=True)
                writer = self._writers[key] = ParquetWriter(os.path.join(folder, self._name), self.compression)
            writer.write(table.take(rows))

    def abort(self) -> None:
        for writer in self._writers.values():
            writer.abort()

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()
        self.messages.append(f"{len(self._writers)} partition(s) écrite(s) dans {self.path}")
