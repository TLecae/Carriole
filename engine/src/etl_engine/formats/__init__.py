"""Lecture et écriture des formats de fichiers. Chaque format expose `read_schema`, `read_batches`, un writer."""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

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


def read_schema(fmt: str, path: str, **opts: Any) -> pa.Schema:
    return _module(fmt).read_schema(path, **opts)


def read_batches(fmt: str, path: str, batch_rows: int, **opts: Any) -> Iterator[pa.Table]:
    return _module(fmt).read_batches(path, batch_rows, **opts)


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
