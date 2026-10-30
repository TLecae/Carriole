"""Sample : prélève un échantillon d'entités (premières, dernières, une sur N, au hasard)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import ConfigError, Emit, Modifier, Params


class Sample(Modifier):
    name = "Sample"
    label = "Échantillonner"
    category = "Filtres"
    mode = "blocking"  # charge toute son entrée
    description = "Prélève un échantillon : les N premières, les N dernières, une entité sur N ou N entités au hasard."
    help = (
        "« first » : les N premières entités reçues. « last » : les N dernières. « every » : une entité sur N (la première, "
        "puis la N+1-ième…). « random » : N entités tirées au hasard (graine fixe : le tirage se reproduit). Les "
        "entités retenues sortent sur « Sampled », les autres sur « Remaining », dans l'ordre d'arrivée. « first » et "
        "« every » travaillent au fil de l'eau ; « last » et « random » doivent lire toutes les entités."
    )
    outputs = ("Sampled", "Remaining")
    rejected = False
    stateful = True

    class Params(Params):
        mode: Literal["first", "last", "every", "random"] = Field(
            "first", title="Méthode", description="first, last, every (une sur N) ou random (au hasard)."
        )
        count: int = Field(100, ge=1, title="N", description="Nombre d'entités (first, last, random) ou pas (every).")
        seed: int = Field(0, title="Graine (random)", description="Même graine, même tirage.")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        return {"Sampled": schema, "Remaining": schema}

    def _emit_split(self, emit: Emit, table: pa.Table, keep: np.ndarray) -> None:
        mask = pa.array(keep)
        if keep.any():
            emit("Sampled", with_crs(table.filter(mask), get_crs(table)))
        if not keep.all():
            emit("Remaining", with_crs(table.filter(pa.array(~keep)), get_crs(table)))

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if p.mode in ("first", "every"):
            seen = 0
            for batch in inputs.get("input", ()):
                self.ctx.check_cancel()
                index = np.arange(seen, seen + batch.num_rows)
                keep = index < p.count if p.mode == "first" else index % p.count == 0
                seen += batch.num_rows
                self._emit_split(emit, batch, keep)
            return
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        n = table.num_rows
        keep = np.zeros(n, dtype=bool)
        if p.mode == "last":
            keep[max(0, n - p.count) :] = True
        else:
            if p.count >= n:
                keep[:] = True
            else:
                keep[np.random.default_rng(p.seed).choice(n, size=p.count, replace=False)] = True
        if not keep.any() and not n:
            raise ConfigError("rien à échantillonner")
        self._emit_split(emit, table, keep)
