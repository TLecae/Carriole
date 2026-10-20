"""ExtractByLocation : garde les entités qui ont une relation spatiale avec des entités « filter »."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries
from etl_engine.sdk import REJECTED, ConfigError, Emit, Modifier, Params, ordered_map, reject
from etl_engine.spatial import Predicate, SpatialIndex, check_same_crs


class ExtractByLocation(Modifier):
    name = "ExtractByLocation"
    label = "Filtre spatial"
    category = "Filtres"
    description = "Sépare les entités « candidate » selon leur relation spatiale avec les entités « filter »."
    help = (
        "Une entité candidate sort sur « Passed » si elle vérifie la relation (intersecte, est dans, contient, "
        "touche, à moins de N unités…) avec **au moins une** entité « filter », sinon sur « Failed ». Les "
        "entités sans géométrie partent sur « Rejected ». Les deux entrées doivent avoir le même système de "
        "coordonnées. « Inverser » échange Passed et Failed."
    )
    mode = "blocking"
    inputs = ("candidate", "filter")
    outputs = ("Passed", "Failed")

    class Params(Params):
        predicate: Predicate = Field(
            "intersects",
            title="Relation",
            description="candidate <relation> filter (« within » : candidate est dans filter).",
        )
        distance: float = Field(0.0, title="Distance (dwithin)", description="Unités du système de coordonnées.")
        invert: bool = Field(
            False, title="Inverser Passed et Failed", description="Échange les sorties Passed et Failed."
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        cand = inputs.get("candidate")
        if cand is not None and GEOM not in cand.names:
            raise ConfigError("les entités « candidate » n'ont pas de géométrie")
        flt = inputs.get("filter")
        if cand is not None and flt is not None:
            check_same_crs(cand, flt)
        return {"Passed": cand, "Failed": cand}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # « filter » est lu et indexé une fois ; les candidates passent lot par lot, plusieurs lots à la fois
        flt = collect(inputs.get("filter", ()))
        if flt is not None and flt.num_rows and GEOM not in flt.column_names:
            raise ConfigError("les entités « filter » n'ont pas de géométrie")
        index = SpatialIndex(flt) if flt is not None and flt.num_rows else None
        p = self.params

        def split(cand: pa.Table) -> tuple[list[tuple[str, pa.Table]], int, int]:
            if GEOM not in cand.column_names:
                raise ConfigError("les entités « candidate » n'ont pas de géométrie")
            geoms = geometries(cand)
            missing = shapely.is_missing(geoms)
            hit = np.zeros(cand.num_rows, dtype=bool)
            if index is not None:
                check_same_crs(cand, index.table)
                ci, _ = index.pairs(geoms, p.predicate, p.distance)
                hit[ci] = True
            passed = ~hit if p.invert else hit
            out = [
                ("Passed", cand.filter(pa.array(passed & ~missing))),
                ("Failed", cand.filter(pa.array(~passed & ~missing))),
            ]
            if missing.any():
                out.append((REJECTED, reject(cand.filter(pa.array(missing)), "pas de géométrie")))
            return out, int((passed & ~missing).sum()), cand.num_rows

        kept = total = 0
        for out, n_kept, n in ordered_map(split, inputs.get("candidate", ()), self.ctx.workers, self.ctx):
            for port, table in out:
                emit(port, table)
            kept, total = kept + n_kept, total + n
        if total:
            self.ctx.info(f"{kept} entité(s) retenue(s) sur {total}")
