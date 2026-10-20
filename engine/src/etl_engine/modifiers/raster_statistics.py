"""RasterStatistics : statistiques globales de chaque raster (minimum, maximum, moyenne, percentiles…)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import rasterio
from pydantic import Field

from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Modifier, Params

Stat = Literal["min", "max", "mean", "std", "count", "nodata_count", "p05", "p25", "p50", "p75", "p95"]
PERCENTILES = {"p05": 5, "p25": 25, "p50": 50, "p75": 75, "p95": 95}
SAMPLE_PIXELS = 4_000_000  # lecture réduite qui sert aux percentiles


class RasterStatistics(Modifier):
    name = "RasterStatistics"
    label = "Statistiques de rasters"
    category = "Rasters"
    description = (
        "Calcule le minimum, le maximum, la moyenne, l'écart-type et des percentiles de chaque raster, en attributs."
    )
    help = (
        "Chaque raster reçoit, pour chaque bande choisie, les statistiques demandées dans des attributs "
        "`bande_statistique` (ndvi_mean…). min, max, mean, std, count (pixels avec donnée) et nodata_count sont exacts "
        "et calculés par fenêtres : la mémoire reste bornée. Les percentiles (p05 à p95) sont estimés sur une lecture "
        "réduite à 4 millions de pixels environ. Utile pour filtrer des scènes (couverture trop faible), choisir une "
        "étendue de couleurs ou contrôler un résultat."
    )
    rejected = False

    class Params(Params):
        bands: list[str] = Field(
            default_factory=list, title="Bandes", description="Numéros (de 1) ou noms ; vide = toutes les bandes."
        )
        stats: list[Stat] = Field(
            default_factory=lambda: ["min", "max", "mean"],
            title="Statistiques",
            description="min, max, mean, std, count, nodata_count, p05, p25, p50, p75, p95.",
            json_schema_extra={"default": ["min", "max", "mean"]},
        )

    def _check(self) -> None:
        if not self.params.stats:
            raise ConfigError("au moins une statistique est nécessaire")

    def _selected(self, desc: dict[str, Any]) -> list[tuple[int, str]]:
        names = desc["band_names"]
        wanted = self.params.bands or [str(i) for i in range(1, desc["count"] + 1)]
        out = []
        for w in wanted:
            if w.strip().isdigit():
                index = int(w)
            elif w in names:
                index = names.index(w) + 1
            else:
                raise ConfigError(f"bande introuvable : {w}")
            if not 1 <= index <= desc["count"]:
                raise ConfigError(f"bande introuvable : {w} (le raster a {desc['count']} bande(s))")
            out.append((index, names[index - 1] if index <= len(names) and names[index - 1] else f"b{index}"))
        return out

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        return {"output": inputs.get("input")}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        descs = core.descriptors(batch)
        rows: list[dict[str, float | None]] = []
        for desc in descs:
            self.ctx.check_cancel()
            row: dict[str, float | None] = {}
            for index, band in self._selected(desc):
                for stat, value in self._band_stats(desc, index).items():
                    row[f"{band}_{stat}"] = value
            rows.append(row)
        out = batch
        for name in dict.fromkeys(k for row in rows for k in row):
            column = pa.array([row.get(name) for row in rows], pa.float64())
            out = (
                out.set_column(out.column_names.index(name), name, column)
                if name in out.column_names
                else out.append_column(name, column)
            )
        return {"output": out}

    def _band_stats(self, desc: dict[str, Any], index: int) -> dict[str, float | None]:
        p = self.params
        acc = {"n": 0, "nodata": 0, "sum": 0.0, "sumsq": 0.0, "min": math.inf, "max": -math.inf}

        def compute(window, cache):
            values, valid = core.read_window(cache.open(desc["uri"]), window, [index])
            v = values[0][valid[0]].astype("float64")
            return np.array(
                [
                    v.size,
                    valid[0].size - v.size,
                    v.sum() if v.size else 0.0,
                    (v * v).sum() if v.size else 0.0,
                    v.min() if v.size else math.inf,
                    v.max() if v.size else -math.inf,
                ]
            )

        def write(_window, partial):
            acc["n"] += int(partial[0])
            acc["nodata"] += int(partial[1])
            acc["sum"] += float(partial[2])
            acc["sumsq"] += float(partial[3])
            acc["min"] = min(acc["min"], float(partial[4]))
            acc["max"] = max(acc["max"], float(partial[5]))

        core.map_windows((desc["height"], desc["width"]), compute, write, check_cancel=self.ctx.check_cancel)
        n = acc["n"]
        result: dict[str, float | None] = {}
        for stat in p.stats:
            if stat == "count":
                result[stat] = float(n)
            elif stat == "nodata_count":
                result[stat] = float(acc["nodata"])
            elif n == 0:
                result[stat] = None
            elif stat == "min":
                result[stat] = acc["min"]
            elif stat == "max":
                result[stat] = acc["max"]
            elif stat == "mean":
                result[stat] = acc["sum"] / n
            elif stat == "std":
                result[stat] = math.sqrt(max(acc["sumsq"] / n - (acc["sum"] / n) ** 2, 0.0))
        wanted = [s for s in p.stats if s in PERCENTILES]
        if wanted and n:
            scale = max(1.0, math.sqrt(desc["width"] * desc["height"] / SAMPLE_PIXELS))
            shape = (max(1, int(desc["height"] / scale)), max(1, int(desc["width"] / scale)))
            with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
                data = ds.read(index, out_shape=shape, masked=True, resampling=rasterio.enums.Resampling.nearest)
            sample = data.compressed().astype("float64")
            for stat in wanted:
                result[stat] = float(np.percentile(sample, PERCENTILES[stat])) if sample.size else None
        elif wanted:
            result.update({s: None for s in wanted})
        return result
