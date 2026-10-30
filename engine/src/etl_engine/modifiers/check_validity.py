"""CheckValidity : contrôle la validité des géométries (la réparation : FixGeometries)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier, Params

REASON = "_invalid_reason"


class CheckValidity(Modifier):
    name = "CheckValidity"
    label = "Valider les géométries"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Sépare les géométries valides des invalides (auto-intersection, anneau non fermé…)."
    help = (
        "Les entités valides sortent sur « Valid », les autres sur « Invalid » avec le motif dans "
        "`_invalid_reason`. Géométrie vide ou absente : « Invalid » (sauf si décoché). Pour corriger les géométries "
        "invalides, utiliser « Réparer les géométries »."
    )
    outputs = ("Valid", "Invalid")
    rejected = False

    class Params(Params):
        reject_empty: bool = Field(
            True,
            title="Refuser les géométries vides ou absentes",
            description=(
                "Coché : une géométrie vide ou absente va sur « Invalid » ; décoché : elle passe sur « Valid »."
            ),
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"Valid": None, "Invalid": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        return {"Valid": schema, "Invalid": schema.append(pa.field(REASON, pa.string()))}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        n = len(geoms)
        missing = shapely.is_missing(geoms)
        empty = shapely.is_empty(geoms)
        valid = shapely.is_valid(geoms) & ~missing
        reasons = np.full(n, None, dtype=object)
        bad = ~valid & ~missing
        if bad.any():
            reasons[bad] = shapely.is_valid_reason(geoms[bad])
        if p.reject_empty:
            reasons[missing] = "pas de géométrie"
            reasons[empty & valid] = "géométrie vide"
        else:
            valid = valid | missing | empty
        invalid = (reasons != None) if p.reject_empty else ~valid  # noqa: E711 - comparaison élément par élément
        crs = get_crs(batch)
        invalid_table = batch.filter(pa.array(invalid)).append_column(
            REASON, pa.array(reasons[invalid].tolist(), pa.string())
        )
        return {"Valid": with_crs(batch.filter(pa.array(~invalid)), crs), "Invalid": with_crs(invalid_table, crs)}
