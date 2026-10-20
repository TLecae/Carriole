"""Formats vecteurs lus et écrits par GDAL/OGR (via pyogrio, mode Arrow) : GeoPackage, GeoJSON, etc.

SQLite / SpatiaLite : GDAL lit et écrit les bases SpatiaLite lui-même (sans la bibliothèque libspatialite) ; une
requête SQL (`sql`, dialecte SQLite) peut remplacer la table lue.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa
import pyogrio
import pyogrio.raw
import shapely

from etl_engine.data import GEOM, get_crs, has_geometry, with_crs
from etl_engine.sdk import ConfigError

OGR_GEOMETRY = "wkb_geometry"  # nom de la colonne WKB dans le flux Arrow de pyogrio

DRIVERS = {
    "gpkg": "GPKG",
    "geojson": "GeoJSON",
    "fgb": "FlatGeobuf",
    "shp": "ESRI Shapefile",
    "tab": "MapInfo File",
    "mif": "MapInfo File",
    "sqlite": "SQLite",
}
MULTI_LAYER = {"gpkg", "sqlite"}  # « overwrite » remplace la couche visée, les autres restent


def _open(path: str, layer: str | None, batch_rows: int, **kw: Any):
    return pyogrio.raw.open_arrow(path, layer=layer, batch_size=batch_rows, use_pyarrow=True, **kw)


def _geometry_name(meta: dict[str, Any], names: list[str]) -> str | None:
    """Colonne WKB du flux Arrow : nom réel de la colonne de la couche, sinon `wkb_geometry`."""
    for candidate in (meta.get("geometry_name"), OGR_GEOMETRY):
        if candidate and candidate in names:
            return candidate
    return None


def _internal(table: pa.Table, meta: dict[str, Any]) -> pa.Table:
    name = _geometry_name(meta, table.column_names)
    if name and name != GEOM:
        table = table.rename_columns([GEOM if n == name else n for n in table.column_names])
    return with_crs(table, meta.get("crs"))


def layers(path: str, **_: Any) -> list[str]:
    return [name for name, _kind in pyogrio.list_layers(path)]


def _sql(sql: str | None) -> dict[str, Any]:
    return {"sql": sql.strip().rstrip(";"), "sql_dialect": "SQLITE"} if sql and sql.strip() else {}


def read_schema(
    path: str, layer: str | None = None, encoding: str | None = None, sql: str | None = None, **_: Any
) -> pa.Schema:
    with _open(path, None if sql else layer, 1, encoding=encoding, **_sql(sql)) as (meta, reader):
        return _internal(reader.schema.empty_table(), meta).schema


def read_batches(
    path: str,
    batch_rows: int,
    layer: str | None = None,
    encoding: str | None = None,
    where: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    columns: list[str] | None = None,
    sql: str | None = None,
    **_: Any,
) -> Iterator[pa.Table]:
    kw: dict[str, Any] = {"encoding": encoding, **_sql(sql)}
    if sql and sql.strip():
        layer = None
    if where:
        kw["where"] = where
    if bbox:
        kw["bbox"] = bbox
    if columns is not None:
        kw["columns"] = [c for c in columns if c != GEOM]
        kw["read_geometry"] = GEOM in columns
    with _open(path, layer, batch_rows, **kw) as (meta, reader):
        for rb in reader:
            yield _internal(pa.Table.from_batches([rb]), meta)


SHP_MAX_BYTES = 2 * 1024**3  # le .shp et le .dbf ne dépassent pas 2 Go
SHP_NAME_LEN = 10


def _geometry_type(table: pa.Table, fmt: str = "gpkg") -> str:
    geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
    present = geoms[~shapely.is_missing(geoms)]
    kinds = Counter(shapely.get_type_id(present).tolist())
    names = {0: "Point", 1: "LineString", 3: "Polygon", 4: "MultiPoint", 5: "MultiLineString", 6: "MultiPolygon"}
    z = " Z" if len(present) and bool(shapely.has_z(present).any()) else ""
    if len(kinds) == 1 and next(iter(kinds)) in names:
        return names[next(iter(kinds))] + z
    if fmt == "shp":  # un Shapefile ne distingue pas simple et multiple : la famille suffit
        for family, name in (({3, 6}, "Polygon"), ({1, 5}, "LineString"), ({0, 4}, "Point")):
            if kinds and set(kinds) <= family:
                return name + z
    return "Unknown"


def shapefile_names(names: list[str]) -> tuple[list[str], dict[str, str]]:
    """Noms de colonnes de 10 caractères au plus, uniques ; renvoie aussi les renommages effectués."""
    used: set[str] = set()
    out, renamed = [], {}
    for name in names:
        if name == GEOM:
            out.append(name)
            continue
        candidate = name[:SHP_NAME_LEN]
        n = 0
        while candidate.lower() in used:
            n += 1
            suffix = f"_{n}"
            candidate = name[: SHP_NAME_LEN - len(suffix)] + suffix
        used.add(candidate.lower())
        out.append(candidate)
        if candidate != name:
            renamed[name] = candidate
    return out, renamed


class OgrWriter:
    """Écrit par lots avec pyogrio. `mode` : create (échoue si le fichier existe), overwrite, append."""

    def __init__(
        self,
        path: str,
        fmt: str = "gpkg",
        layer: str | None = None,
        mode: str = "overwrite",
        encoding: str | None = None,
        geometry_type: str | None = None,
        **_: Any,
    ) -> None:
        self.path, self.layer, self.mode, self.encoding = path, layer, mode, encoding
        self.driver = DRIVERS[fmt]
        self.fmt = fmt
        self.geometry_type = geometry_type
        self.messages: list[str] = []
        self._pending: list[pa.Table] = []
        if fmt == "shp" and not encoding:
            self.encoding = "UTF-8"  # écrit un .cpg : les accents survivent à la relecture
        self._started = False
        if mode == "create" and os.path.exists(path):
            raise ConfigError(f"le fichier existe déjà : {path}")

    def write(self, table: pa.Table) -> None:
        if self.fmt == "mif":  # MIF/MID ne s'ouvre pas en ajout : on écrit en une fois à la fermeture
            self._pending.append(table)
            return
        self._write(table)

    def write_stream(self, tables: Iterable[pa.Table]) -> None:
        """Écrit tous les lots d'un seul appel GDAL : bien plus rapide qu'un appel par lot (GeoPackage : ×4)."""
        iterator = iter(tables)
        first = next(iterator, None)
        if first is None:
            return
        if self.fmt == "mif":
            self._pending.extend([first, *iterator])
            return
        self._write(first, iterator)

    def _prepare(self, table: pa.Table) -> pa.Table:
        if self.fmt == "shp":
            new_names, _ = shapefile_names(table.column_names)
            table = table.rename_columns(new_names)
        return table.replace_schema_metadata(None)

    def _write(self, table: pa.Table, rest: Iterable[pa.Table] = ()) -> None:
        crs = get_crs(table)
        geometry_name = None
        if has_geometry(table):
            geometry_name = GEOM
            if self.geometry_type is None:
                self.geometry_type = _geometry_type(table, self.fmt)
        if self.fmt == "shp":
            _, renamed = shapefile_names(table.column_names)
            if renamed and not self._started:
                listing = ", ".join(f"{old} → {new}" for old, new in renamed.items())
                self.messages.append(
                    f"Shapefile : {len(renamed)} nom(s) de colonne raccourci(s) à 10 caractères : {listing}"
                )
        first = self._prepare(table)
        data: Any = first
        iterator = iter(rest)
        head = next(iterator, None)
        if head is not None:  # plusieurs lots : on les présente à GDAL comme un flux

            def batches() -> Iterator[pa.RecordBatch]:
                yield from first.to_batches()
                yield from self._prepare(head).to_batches()
                for more in iterator:
                    yield from self._prepare(more).to_batches()

            data = pa.RecordBatchReader.from_batches(first.schema, batches())
        append = self.mode == "append" or self._started
        if not self._started and self.mode == "overwrite" and self.fmt not in MULTI_LAYER and os.path.exists(self.path):
            os.remove(self.path)
        creation: dict[str, Any] = {"FORMAT": "MIF"} if self.fmt == "mif" else {}
        if self.fmt == "sqlite" and not os.path.exists(self.path):
            creation["SPATIALITE"] = "YES"  # base SpatiaLite (métadonnées géométriques lues par QGIS, FME…)
        pyogrio.raw.write_arrow(
            data,
            self.path,
            layer=self.layer,
            driver=self.driver,
            geometry_name=geometry_name,
            geometry_type=self.geometry_type if geometry_name else None,
            crs=crs,
            encoding=self.encoding,
            append=append,
            **creation,
        )
        self._started = True
        if self.fmt == "shp":
            self._check_size()

    def _check_size(self) -> None:
        stem = os.path.splitext(self.path)[0]
        for ext in (".shp", ".dbf"):
            size = os.path.getsize(stem + ext) if os.path.exists(stem + ext) else 0
            if size >= SHP_MAX_BYTES * 0.95:
                raise ConfigError(
                    f"{os.path.basename(stem + ext)} approche la limite de 2 Go du Shapefile ({size // 1024**2} Mo) : "
                    "écrire en GeoPackage ou en GeoParquet"
                )

    def abort(self) -> None:
        self._pending = []  # MIF : rien n'est écrit si le flux a échoué

    def close(self) -> None:
        if self._pending:
            pending, self._pending = self._pending, []
            self._write(pa.concat_tables(pending))
