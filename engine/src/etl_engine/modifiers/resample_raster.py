"""ResampleRaster : change la taille des pixels, ou aligne un raster sur la grille d'un autre."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.modifiers.warp_raster import Resampling, warp_rasters
from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Emit, Modifier, Params


class ResampleRaster(Modifier):
    name = "ResampleRaster"
    label = "Rééchantillonner / aligner"
    category = "Rasters"
    description = "Change la taille des pixels d'un raster, ou l'aligne sur la grille d'un autre raster (même pixels, même emprise)."
    help = (
        "Sans « Aligner sur un raster de référence » : le raster garde son système et son emprise, avec des pixels de "
        "« Taille du pixel » (ou multipliés par « Facteur » : 2 = pixels deux fois plus grands). Avec la référence "
        "(deuxième entrée) : le raster est recalculé sur exactement la grille de la référence (système, taille et "
        "emprise), ce qui permet de combiner des images de résolutions différentes. nearest convient aux classes, "
        "bilinear / cubic aux valeurs continues, average pour réduire la résolution."
    )
    rejected = False

    class Params(Params):
        align: bool = Field(
            False,
            title="Aligner sur un raster de référence",
            description="Ajoute une entrée « reference » dont la grille est reprise.",
        )
        cell_size: float = Field(
            0.0, ge=0, title="Taille du pixel", description="Dans l'unité du système ; 0 = ne pas fixer."
        )
        factor: float = Field(
            1.0, gt=0, title="Facteur", description="Multiplie la taille du pixel (2 = deux fois plus grand)."
        )
        resampling: Resampling = Field(
            "bilinear", title="Rééchantillonnage", description="Méthode de calcul des nouveaux pixels."
        )
        nodata: float | None = Field(None, title="Valeur sans donnée", description="Vide = celle du raster.")

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (("input", "reference") if getattr(params, "align", False) else ("input",)), cls.outputs

    def _check(self) -> None:
        p = self.params
        if not p.align and not p.cell_size and p.factor == 1.0:
            raise ConfigError("indiquer une taille de pixel, un facteur, ou une référence")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        return {"output": inputs.get("input") or core.raster_schema()}

    def _scaled(self, desc: dict[str, Any]) -> dict[str, Any]:
        p = self.params
        a, b, c, d, e, f = desc["transform"]
        xres, yres = core.pixel_size(desc)
        new = p.cell_size or None
        sx = (new / xres) if new else p.factor
        sy = (new / yres) if new else p.factor
        width, height = max(1, math.ceil(desc["width"] / sx)), max(1, math.ceil(desc["height"] / sy))
        return {
            "crs": desc["crs"],
            "transform": [a * sx, b * sy, c, d * sx, e * sy, f],
            "width": width,
            "height": height,
        }

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        reference = None
        if p.align:
            ref_table = collect(inputs.get("reference", ()))
            if ref_table is None or ref_table.num_rows == 0:
                raise ConfigError("la référence ne contient aucun raster")
            reference = core.grid_of(core.descriptors(ref_table)[0])
        for batch in inputs.get("input", ()):
            grid_for = (lambda _desc: reference) if reference else self._scaled
            emit("output", warp_rasters(self.ctx, batch, grid_for, p.resampling, p.nodata, "resampled"))
