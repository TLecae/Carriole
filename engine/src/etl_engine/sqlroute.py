"""Voie d'exécution SQL : un modificateur qui sait s'exprimer en SQL est exécuté par DuckDB, directement sur les
fichiers Parquet des ports (voir ADR 0003).

Avantages : DuckDB parallélise la requête lui-même, lit et écrit en flux, et **déborde sur disque** quand la
mémoire autorisée est atteinte (tri, jointure, agrégation de volumes supérieurs à la mémoire). La voie Python
(lots Arrow) reste la référence : un modificateur qui ne propose pas de SQL, ou dont les paramètres ne s'y
prêtent pas, s'exécute par elle, avec les mêmes résultats.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from etl_engine import duck
from etl_engine.data import get_crs
from etl_engine.sdk import ConfigError, Modifier


@dataclass
class SqlRouteResult:
    rows: dict[str, int] = field(default_factory=dict)
    rows_in: dict[str, int] = field(default_factory=dict)


def has_sql_route(inst: Modifier) -> bool:
    return type(inst).sql is not Modifier.sql


def _quote_path(path: Path | str) -> str:
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def _kv_metadata(schema: pa.Schema | None) -> str:
    crs = get_crs(schema) if schema is not None else None
    return f", KV_METADATA {{'etl.crs': {_quote_path(crs)}}}" if crs else ""


def run_sql_route(
    inst: Modifier,
    port_files: dict[str, list[Path]],
    out_paths: dict[str, Path],
    out_schemas: dict[str, pa.Schema | None],
    in_schemas: dict[str, pa.Schema | None],
    spill_dir: Path,
) -> SqlRouteResult | None:
    """Exécute `inst` en SQL ; None si le modificateur n'a pas de voie SQL pour ces paramètres ou si une entrée est
    vide (la voie Python sait gérer ces cas)."""
    if not has_sql_route(inst) or any(not files for files in port_files.values()):
        return None
    relations = {port: f"in_{i}" for i, port in enumerate(port_files)}
    queries = inst.sql(relations, in_schemas)
    if queries is None:
        return None

    con = duck.connect("heavy", spill_dir)  # part du budget du run ; au-delà, DuckDB déborde sur disque
    result = SqlRouteResult()
    try:
        for port, files in port_files.items():
            listing = ", ".join(_quote_path(f) for f in files)
            con.execute(f"CREATE VIEW {relations[port]} AS SELECT * FROM read_parquet([{listing}], union_by_name=true)")
            result.rows_in[port] = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        for port, query in queries.items():
            target = out_paths[port]
            tmp = target.with_suffix(".parquet.tmp")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                con.execute(
                    f"COPY ({query}) TO {_quote_path(tmp)} "
                    f"(FORMAT parquet, COMPRESSION zstd{_kv_metadata(out_schemas.get(port))})"
                )
            except duckdb.OutOfMemoryException as exc:
                tmp.unlink(missing_ok=True)
                raise MemoryError(str(exc).splitlines()[0]) from None
            except duckdb.Error as exc:
                tmp.unlink(missing_ok=True)
                raise ConfigError(f"SQL ({inst.name}) : {str(exc).splitlines()[0]}") from None
            os.replace(tmp, target)
            result.rows[port] = pq.ParquetFile(target).metadata.num_rows
        return result
    finally:
        con.close()
        shutil.rmtree(spill_dir, ignore_errors=True)
