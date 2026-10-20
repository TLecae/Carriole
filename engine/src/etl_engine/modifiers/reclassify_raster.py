"""ReclassifyRaster : remplace des plages de valeurs par des classes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Modifier, Params

DTYPES = Literal["uint8", "uint16", "int16", "int32", "float32"]


class ReclassRule(Params):
    min: float | None = Field(None, title="De", description="Borne basse (incluse) ; vide = sans borne.")
    max: float | None = Field(
        None, title="À", description="Borne haute (exclue, ou incluse si coché) ; vide = sans borne."
    )
    include_max: bool = Field(
        False, title="Inclure la borne haute", description="Coché : la valeur « À » fait partie de la plage."
    )
    value: float = Field(0.0, title="Classe", description="Valeur donnée aux pixels de la plage.")


class ReclassifyRaster(Modifier):
    name = "ReclassifyRaster"
    label = "Reclasser un raster"
    category = "Rasters"
    description = (
        "Remplace des plages de valeurs d'un raster par des classes (pente en 4 classes, NDVI en eau/sol/végétation)."
    )
    help = (
        "Chaque règle donne une plage [De ; À[ et la classe correspondante ; les règles sont examinées dans l'ordre, la "
        "première qui convient l'emporte. Pour une valeur exacte, mettre la même valeur en « De » et « À » et cocher "
        "« Inclure la borne haute ». « Autres pixels » : sans donnée, conservés tels quels (keep) ou remplacés par « Valeur "
        "par défaut ». Les pixels sans donnée restent sans donnée. Le résultat est une bande « class »."
    )
    rejected = False

    class Params(Params):
        band: int = Field(1, ge=1, title="Bande", description="Bande à reclasser (à partir de 1).")
        rules: list[ReclassRule] = Field(
            default_factory=list, title="Règles", description="Plages et classes, examinées dans l'ordre."
        )
        others: Literal["nodata", "keep", "default"] = Field(
            "nodata",
            title="Autres pixels",
            description="nodata : sans donnée ; keep : valeur d'origine ; default : « Valeur par défaut ».",
        )
        default_value: float = Field(0.0, title="Valeur par défaut", description="Pour « Autres pixels » = default.")
        dtype: DTYPES = Field("uint8", title="Type du résultat", description="Type des pixels du résultat.")
        nodata: float | None = Field(
            None, title="Valeur sans donnée", description="Vide = 255 pour uint8, convention du type sinon."
        )

    def _check(self) -> None:
        if not self.params.rules:
            raise ConfigError("au moins une règle est nécessaire")
        for r in self.params.rules:
            if r.min is not None and r.max is not None and r.min > r.max:
                raise ConfigError(f"règle invalide : de {r.min} à {r.max}")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        return {"output": inputs.get("input") or core.raster_schema()}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        descs = core.descriptors(batch)
        attributes = core.attributes_of(batch)
        outputs = []
        nodata = core.nodata_for(p.dtype, p.nodata)
        for i, desc in enumerate(descs):
            self.ctx.check_cancel()
            if p.band > desc["count"]:
                raise ConfigError(f"le raster n'a que {desc['count']} bande(s)")

            def compute(window, cache, desc=desc):
                values, valid = core.read_window(cache.open(desc["uri"]), window, [p.band])
                v = values[0]
                out = np.full(v.shape, np.nan, dtype="float64")
                done = np.zeros(v.shape, dtype=bool)
                for rule in p.rules:
                    hit = valid[0] & ~done
                    if rule.min is not None:
                        hit &= v >= rule.min
                    if rule.max is not None:
                        hit &= (v <= rule.max) if rule.include_max else (v < rule.max)
                    out[hit] = rule.value
                    done |= hit
                rest = valid[0] & ~done
                if p.others == "keep":
                    out[rest] = v[rest]
                elif p.others == "default":
                    out[rest] = p.default_value
                result = np.where(np.isnan(out), nodata if nodata is not None else 0, out)
                if np.issubdtype(np.dtype(p.dtype), np.integer):
                    result = np.rint(result)
                return result[None].astype(p.dtype)

            outputs.append(
                core.run_blocks(
                    self.ctx,
                    f"reclass_{i}",
                    core.grid_of(desc),
                    compute,
                    count=1,
                    dtype=p.dtype,
                    nodata=nodata,
                    band_names=["class"],
                )
            )
        return {"output": core.to_table(outputs, attributes)}
