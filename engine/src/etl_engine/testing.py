"""Harnais pytest pour tester un modificateur sans lancer de flux.

from etl_engine.testing import run_modifier, table

def test_ma_regle():
    out = run_modifier(MonModifier, {"attribut": "a"}, table(a=[1, 2, 3]))
    assert out["output"].column("a").to_pylist() == [1, 2, 3]
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import shapely

from etl_engine.data import GEOM, with_crs
from etl_engine.sdk import Context, Modifier


def table(crs: str | None = None, **columns: list[Any]) -> pa.Table:
    """Table à partir de colonnes Python : `table(id=[1, 2], nom=["a", "b"])`."""
    return with_crs(pa.table(columns), crs)


def points(xs: list[float], ys: list[float], crs: str | None = "EPSG:2154", **columns: list[Any]) -> pa.Table:
    """Table de points avec colonne `geometry` (WKB)."""
    geoms = shapely.points(np.asarray(xs, dtype=float), np.asarray(ys, dtype=float))
    t = pa.table(columns) if columns else pa.table({"_n": list(range(len(xs)))})
    return with_crs(t.append_column(GEOM, pa.array(shapely.to_wkb(geoms), pa.binary())), crs)


def run_modifier(
    cls: type[Modifier],
    params: dict[str, Any] | None = None,
    inputs: pa.Table | Mapping[str, pa.Table] | None = None,
    batch_rows: int = 3,
    ctx: Context | None = None,
) -> dict[str, pa.Table]:
    """Exécute `cls` sur `inputs` (une table pour l'entrée unique, ou un dict par port) et renvoie une table par sortie.

    Les entrées sont découpées en petits lots (`batch_rows`) pour vérifier que le traitement par lots est correct.
    """
    inst = cls(cls.Params.model_validate(params or {}), ctx or Context(batch_rows=batch_rows))
    if isinstance(inputs, pa.Table):
        inputs = {cls.ports(inst.params)[0][0]: inputs}
    sources = {port: _chunks(t, batch_rows) for port, t in (inputs or {}).items()}
    collected: dict[str, list[pa.Table]] = {}

    def emit(port: str, t: pa.Table) -> None:
        collected.setdefault(port, []).append(t)

    inst.run(sources, emit)
    out: dict[str, pa.Table] = {}
    for port in cls.ports(inst.params)[1]:
        parts = collected.get(port, [])
        out[port] = pa.concat_tables(parts, promote_options="default") if parts else pa.table({})
    return out


def _chunks(t: pa.Table, size: int) -> list[pa.Table]:
    return [t.slice(i, size) for i in range(0, max(t.num_rows, 1), size)] if t.num_rows else []


run_transformer = run_modifier  # ancien nom (avant le 2026-10-20)
