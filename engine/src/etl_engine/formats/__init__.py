"""Lecture et écriture des formats de fichiers. Chaque format expose `read_schema`, `read_batches`, un writer."""

from __future__ import annotations

import os
import threading
import time
import urllib.request
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from etl_engine import duck
from etl_engine.formats import csvfmt, ogr, parquet, xlsx
from etl_engine.sdk import ConfigError

EXTENSIONS = {
    ".parquet": "parquet",
    ".geoparquet": "parquet",
    ".gpkg": "gpkg",
    ".csv": "csv",
    ".txt": "csv",
    ".tsv": "csv",
    ".geojson": "geojson",
    ".fgb": "fgb",
    ".shp": "shp",
    ".tab": "tab",
    ".mif": "mif",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".sqlite": "sqlite",
    ".spatialite": "sqlite",
    ".db": "sqlite",
}
FORMATS = sorted(set(EXTENSIONS.values()))


is_remote = parquet.is_remote  # adresse web (http, https) : seul le GeoParquet se lit ainsi


def remote_stamp(url: str) -> str:
    """Version d'un fichier web, pour le cache des runs : ETag, sinon date et taille (requête HEAD, proxy des
    paramètres). Sans aucune des deux, ou injoignable : une valeur nouvelle à chaque fois (le fichier est relu)."""
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - http(s) seulement (is_remote)
            headers = response.headers
    except (OSError, ValueError):
        return f"?{time.time_ns()}"
    found = headers.get("ETag") or ":".join(filter(None, [headers.get("Last-Modified"), headers.get("Content-Length")]))
    return found or f"?{time.time_ns()}"


class Writer(Protocol):
    """`abort` (facultatif) annule ce qui peut l'être ; `messages` (facultatif) : notes à journaliser."""

    def write(self, table: pa.Table) -> None: ...
    def close(self) -> None: ...


def detect(path: str) -> str:
    fmt = EXTENSIONS.get(Path(path.split("*")[0] if "*" in path else path).suffix.lower()) or EXTENSIONS.get(
        Path(path).suffix.lower()
    )
    if fmt is None:
        raise ConfigError(f"format non reconnu pour « {path} » : choisir le format explicitement")
    return fmt


def _module(fmt: str):
    if fmt == "parquet":
        return parquet
    if fmt == "csv":
        return csvfmt
    if fmt == "xlsx":
        return xlsx
    if fmt in ogr.DRIVERS:
        return ogr
    raise ConfigError(f"format inconnu : {fmt}")


_schemas: OrderedDict[tuple, pa.Schema] = OrderedDict()
_schemas_lock = threading.Lock()
SCHEMAS_KEPT = 256


def read_schema(fmt: str, path: str, **opts: Any) -> pa.Schema:
    """Schéma d'un fichier, mémorisé par fichier (date, taille) et réglages : l'éditeur le redemande à chaque
    modification du flux, un run aussi, et GDAL relit tout un GeoJSON pour le donner (8 s pour 1 M de points)."""
    key = None
    if not is_remote(path) and not any(c in path for c in "*?["):
        try:
            st = os.stat(path)
            key = (fmt, os.path.abspath(path), st.st_mtime_ns, st.st_size, repr(sorted(opts.items())))
        except OSError:
            key = None  # fichier introuvable : l'erreur vient de la lecture
    if key is not None:
        with _schemas_lock:
            if key in _schemas:
                _schemas.move_to_end(key)
                return _schemas[key]
    schema = _module(fmt).read_schema(path, **opts)
    if key is not None:
        with _schemas_lock:
            _schemas[key] = schema
            while len(_schemas) > SCHEMAS_KEPT:
                _schemas.popitem(last=False)
    return schema


def read_batches(fmt: str, path: str, batch_rows: int, **opts: Any) -> Iterator[pa.Table]:
    return _module(fmt).read_batches(path, batch_rows, **opts)


def sql_query(fmt: str, sources: list[str], limit: int | None = None, **opts: Any) -> str | None:
    """Requête DuckDB qui lit `sources` comme `read_batches` (voie SQL des lecteurs), ou None si ce format ou ces
    réglages ne s'y prêtent pas : la lecture passe alors par `read_batches`."""
    if fmt == "parquet":
        return parquet.sql_query(sources, opts.get("add_filename", False), limit)
    if fmt == "csv" and len(sources) == 1:
        return csvfmt.sql_query(sources[0], limit=limit, **opts)
    return None


def as_port(fmt: str, sources: list[str], limit: int | None = None, **opts: Any) -> str | None:
    """Le fichier lu, s'il peut servir tel quel de port (ADR 0043) ; None : il est lu et recopié dans le port."""
    if fmt == "parquet":
        return parquet.as_port(sources, opts.get("add_filename", False), limit)
    return None


def sql_prepare(fmt: str, con: Any, sources: list[str], limit: int | None = None, **opts: Any) -> None:
    """Prépare la connexion de `sql_query` : extension web au besoin ; extension spatiale seulement pour construire
    une géométrie (CSV), son premier chargement coûte plus que la lecture d'un million de lignes."""
    if fmt == "csv" and (opts.get("wkt_column") or (opts.get("x_column") and opts.get("y_column"))):
        duck.load_spatial(con)
    if fmt == "parquet":
        parquet.sql_prepare(con, sources, limit)


def open_writer(fmt: str, path: str, **opts: Any) -> Writer:
    if fmt == "parquet":
        if opts.get("partition_by"):
            return parquet.PartitionedParquetWriter(path, **opts)
        return parquet.ParquetWriter(path, **{k: v for k, v in opts.items() if k == "compression"})
    if fmt == "csv":
        return csvfmt.CsvWriter(path, **opts)
    if fmt == "xlsx":
        return xlsx.XlsxWriter(path, **opts)
    if fmt in ogr.DRIVERS:
        return ogr.OgrWriter(path, fmt=fmt, **opts)
    raise ConfigError(f"format inconnu : {fmt}")
