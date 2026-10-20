"""CSV : séparateur et encodage détectés ; géométrie depuis une colonne WKT ou des colonnes X/Y."""

from __future__ import annotations

import codecs
import contextlib
import os
from collections.abc import Iterator
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import shapely

from etl_engine import duck
from etl_engine.atomic import part_name
from etl_engine.data import GEOM, has_geometry, with_crs
from etl_engine.sdk import ConfigError


def detect_encoding(path: str) -> str:
    """utf-8 si le début du fichier est décodable (BOM compris), sinon latin-1 (≈ cp1252)."""
    with open(path, "rb") as fh:
        head = fh.read(256 * 1024)
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as exc:
        # une coupure en fin de tampon au milieu d'un caractère ne prouve rien
        return "utf-8" if exc.start >= len(head) - 4 else "latin-1"


def _relation(
    path: str, delimiter: str | None, encoding: str | None, header: bool, detect_types: bool, add_filename: bool
):
    con = duck.connect("heavy")
    enc = encoding or (detect_encoding(path) if "*" not in path and "?" not in path else "utf-8")
    args = ["header=?", "all_varchar=?", "encoding=?", "union_by_name=true", "filename=?"]
    values: list[Any] = [header, not detect_types, enc, add_filename]
    if delimiter:
        args.append("delim=?")
        values.append(delimiter)
    sql = f"SELECT * FROM read_csv(?, {', '.join(args)})"
    return con, sql, [path, *values]


def _geometry(
    table: pa.Table, wkt_column: str | None, x_column: str | None, y_column: str | None, crs: str | None
) -> pa.Table:
    if wkt_column:
        if wkt_column not in table.column_names:
            raise ConfigError(f"colonne WKT absente : {wkt_column}")
        geoms = shapely.from_wkt(table.column(wkt_column).to_numpy(zero_copy_only=False), on_invalid="ignore")
        table = table.drop_columns([wkt_column])
    elif x_column and y_column:
        for c in (x_column, y_column):
            if c not in table.column_names:
                raise ConfigError(f"colonne de coordonnées absente : {c}")
        x = pc.cast(pc.replace_substring(table.column(x_column), ",", "."), pa.float64(), safe=False)
        y = pc.cast(pc.replace_substring(table.column(y_column), ",", "."), pa.float64(), safe=False)
        geoms = shapely.points(np.column_stack([x.to_numpy(zero_copy_only=False), y.to_numpy(zero_copy_only=False)]))
    else:
        return table
    wkb = pa.array(shapely.to_wkb(geoms), type=pa.binary())
    return with_crs(table.append_column(GEOM, wkb), crs)


def read_schema(
    path: str,
    delimiter: str | None = None,
    encoding: str | None = None,
    header: bool = True,
    detect_types: bool = False,
    add_filename: bool = False,
    wkt_column: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    crs: str | None = None,
    **_: Any,
) -> pa.Schema:
    con, sql, args = _relation(path, delimiter, encoding, header, detect_types, add_filename)
    empty = con.execute(f"{sql} LIMIT 0", args).to_arrow_table()
    return _geometry(empty, wkt_column, x_column, y_column, crs).schema


def read_batches(
    path: str,
    batch_rows: int,
    delimiter: str | None = None,
    encoding: str | None = None,
    header: bool = True,
    detect_types: bool = False,
    add_filename: bool = False,
    wkt_column: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    crs: str | None = None,
    **_: Any,
) -> Iterator[pa.Table]:
    con, sql, args = _relation(path, delimiter, encoding, header, detect_types, add_filename)
    reader = con.execute(sql, args).to_arrow_reader(batch_rows)
    for rb in reader:
        yield _geometry(pa.Table.from_batches([rb]), wkt_column, x_column, y_column, crs)


class CsvWriter:
    """Écrit un CSV UTF-8 ; la géométrie devient une colonne WKT `wkt`."""

    def __init__(self, path: str, delimiter: str = ",", mode: str = "overwrite", **_: Any) -> None:
        if mode == "create" and os.path.exists(path):
            raise ConfigError(f"le fichier existe déjà : {path}")
        self.path, self.delimiter, self.mode = path, delimiter, mode
        # tout ou rien : le fichier n'apparaît qu'à la fermeture (sauf en ajout, qui complète un fichier existant)
        self._target = path if mode == "append" else part_name(path)
        self._writer: pacsv.CSVWriter | None = None
        self._sink = None

    def _flat(self, table: pa.Table) -> pa.Table:
        if has_geometry(table):
            geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
            wkt = pa.array(shapely.to_wkt(geoms, rounding_precision=9, trim=True), type=pa.string())
            table = table.drop_columns([GEOM]).append_column("wkt", wkt)
        return table.replace_schema_metadata(None)

    def write(self, table: pa.Table) -> None:
        flat = self._flat(table)
        if self._writer is None:
            appending = self.mode == "append"
            self._sink = open(self._target, "ab" if appending else "wb")  # noqa: SIM115 - fermé dans close()
            opts = pacsv.WriteOptions(delimiter=self.delimiter, include_header=not appending)
            self._writer = pacsv.CSVWriter(self._sink, flat.schema, write_options=opts)
        self._writer.write_table(flat)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._sink.close()
            self._writer = None
            if self._target != self.path:
                os.replace(self._target, self.path)

    def abort(self) -> None:
        """Écriture interrompue : le fichier de destination n'est pas touché (sauf en ajout)."""
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
            self._sink.close()
            self._writer = None
            if self._target != self.path:
                with contextlib.suppress(OSError):
                    os.unlink(self._target)
