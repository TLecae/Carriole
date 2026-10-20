"""CheckValidity : contrôle (et répare) la validité des géométries."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, replace_geometries, with_crs
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
        "`_invalid_reason`. Avec « Réparer », les géométries invalides sont corrigées (make_valid) et sortent sur "
        "« Valid » (le motif d'origine est conservé dans `_repaired_reason`) ; seules celles qui restent "
        "irréparables vont sur « Invalid ». Géométrie vide ou absente : « Invalid »."
    )
    outputs = ("Valid", "Invalid")
    rejected = False

    class Params(Params):
        repair: bool = Field(
            False,
            title="Réparer",
            description=(
                "Corrige les géométries invalides (make_valid) au lieu de les envoyer sur « Invalid ». Pour "
                "régler la réparation, voir « Réparer les géométries »."
            ),
        )
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
        valid = schema.append(pa.field("_repaired_reason", pa.string())) if self.params.repair else schema
        return {"Valid": valid, "Invalid": schema.append(pa.field(REASON, pa.string()))}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        n = len(geoms)
        missing = shapely.is_missing(geoms)
        empty = shapely.is_empty(geoms)
        valid = shapely.is_valid(geoms) & ~missing
        reasons = np.array([None] * n, dtype=object)
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
        repaired_reason = np.array([None] * n, dtype=object)
        out_geoms = geoms
        ok_now = np.zeros(n, dtype=bool)  # réparées
        if p.repair and bad.any():
            fixed = np.array(geoms, dtype=object)
            fixed[bad] = shapely.make_valid(geoms[bad])
            ok_now = bad & ~shapely.is_missing(fixed) & ~shapely.is_empty(fixed)
            out_geoms = np.where(ok_now, fixed, geoms)
            repaired_reason[ok_now] = reasons[ok_now]
            invalid = invalid & ~ok_now
        good = ~invalid
        valid_table = batch.filter(pa.array(good))
        if p.repair:
            # seules les géométries réparées sont réencodées ; les autres gardent leur WKB d'origine
            valid_table = replace_geometries(valid_table, out_geoms[good], ok_now[good])
            valid_table = valid_table.append_column(
                "_repaired_reason", pa.array(repaired_reason[good].tolist(), pa.string())
            )
        invalid_table = batch.filter(pa.array(invalid)).append_column(
            REASON, pa.array(reasons[invalid].tolist(), pa.string())
        )
        return {"Valid": with_crs(valid_table, crs), "Invalid": with_crs(invalid_table, crs)}
