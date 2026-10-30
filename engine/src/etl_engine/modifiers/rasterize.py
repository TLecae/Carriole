"""Rasterize : transforme des entités vectorielles (polygones, lignes, points) en raster."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field
from rasterio.enums import MergeAlg
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.windows import transform as window_transform

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs
from etl_engine.raster import core
from etl_engine.sdk import Attribute, ConfigError, Crs, Emit, Modifier, Params

DTYPES = Literal["uint8", "uint16", "int16", "int32", "float32", "float64"]
MAX_PIXELS = 400_000_000


class Rasterize(Modifier):
    name = "Rasterize"
    label = "Entités vers raster"
    category = "Rasters"
    mode = "blocking"  # charge toute son entrée
    description = "Transforme des polygones, lignes ou points en raster : valeur d'un attribut ou valeur fixe."
    help = (
        "Les entités sont dessinées dans un raster de pixels de « Taille du pixel » (dans l'unité de leur système de "
        "coordonnées), sur leur emprise ou sur celle de « Emprise ». Chaque pixel prend la valeur de « Attribut de la "
        "valeur » de l'entité qui le recouvre (la dernière en cas de recouvrement, ou la somme avec « add »), ou la "
        "« Valeur fixe » (1 par défaut : un masque). Les pixels non recouverts valent « Valeur de fond » (sans "
        "donnée si vide). « Pixels touchés » dessine tout pixel que l'entité touche, utile pour des lignes fines. "
        "Le dessin se fait par fenêtres de 512 pixels : la taille du raster n'est limitée que par le disque."
    )
    rejected = False

    class Params(Params):
        value_attribute: Attribute = Field("", title="Attribut de la valeur", description="Vide = valeur fixe.")
        burn_value: float = Field(
            1.0, title="Valeur fixe", description="Valeur des pixels recouverts si aucun attribut n'est choisi."
        )
        cell_size: float = Field(
            10.0, gt=0, title="Taille du pixel", description="Dans l'unité du système de coordonnées des entités."
        )
        extent: list[float] = Field(
            default_factory=list,
            title="Emprise",
            description="Ouest, Sud, Est, Nord ; vide = l'emprise des entités.",
            examples=[[600000, 6500000, 601000, 6501000]],
        )
        crs: Crs = Field("", title="Système de coordonnées", description="Vide = celui des entités.")
        dtype: DTYPES = Field("float32", title="Type", description="Type des pixels.")
        background: float | None = Field(None, title="Valeur de fond", description="Vide = sans donnée.")
        all_touched: bool = Field(False, title="Pixels touchés", description="Dessine tout pixel touché par l'entité.")
        merge: Literal["replace", "add"] = Field(
            "replace", title="Recouvrements", description="replace : la dernière l'emporte ; add : somme."
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if self.params.extent and len(self.params.extent) != 4:
            raise ConfigError("l'emprise demande quatre nombres : ouest, sud, est, nord")
        return {"output": core.raster_schema(crs=get_crs(schema) if schema is not None else None)}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if p.extent and len(p.extent) != 4:
            raise ConfigError("l'emprise demande quatre nombres : ouest, sud, est, nord")
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            self.ctx.warning("aucune entité reçue ; rien n'est produit")
            return
        if GEOM not in table.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if p.value_attribute and p.value_attribute not in table.column_names:
            raise ConfigError(f"attribut absent : {p.value_attribute}")
        geoms = geometries(table)
        live = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
        geoms = geoms[live]
        if not len(geoms):
            self.ctx.warning("aucune géométrie ; rien n'est produit")
            return
        burn = (
            np.asarray(
                table.column(p.value_attribute)
                .cast(pa.float64())
                .fill_null(float("nan"))
                .to_numpy(zero_copy_only=False)
            )[live]
            if p.value_attribute
            else np.full(len(geoms), p.burn_value)
        )
        keep = ~np.isnan(burn)  # une entité sans valeur n'est pas dessinée
        geoms, burn = geoms[keep], burn[keep]
        xmin, ymin, xmax, ymax = p.extent or shapely.total_bounds(geoms)
        width, height = max(1, math.ceil((xmax - xmin) / p.cell_size)), max(1, math.ceil((ymax - ymin) / p.cell_size))
        if width * height > MAX_PIXELS:
            raise ConfigError(
                f"raster trop grand ({width} x {height} pixels) : augmenter la taille du pixel ou réduire l'emprise"
            )
        transform = from_origin(xmin, ymin + height * p.cell_size, p.cell_size, p.cell_size)
        crs = p.crs or get_crs(table)
        nodata = core.nodata_for(p.dtype, p.background)
        fill = nodata if nodata is not None else 0
        tree = shapely.STRtree(geoms)
        grid = {
            "crs": crs,
            "transform": [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f],
            "width": width,
            "height": height,
        }
        alg = MergeAlg.add if p.merge == "add" else MergeAlg.replace

        def compute(window, cache):
            local = window_transform(window, transform)
            box = shapely.box(*rasterio_bounds(local, int(window.width), int(window.height)))
            hits = np.sort(tree.query(box, predicate="intersects"))
            if not len(hits):
                return np.full((1, int(window.height), int(window.width)), fill, dtype=p.dtype)
            shapes = [(geoms[i], float(burn[i])) for i in hits]
            out = rasterize(
                shapes,
                out_shape=(int(window.height), int(window.width)),
                transform=local,
                fill=fill,
                all_touched=p.all_touched,
                dtype=p.dtype,
                merge_alg=alg,
            )
            return out[None]

        desc = core.run_blocks(
            self.ctx,
            "rasterized",
            grid,
            compute,
            count=1,
            dtype=p.dtype,
            nodata=nodata,
            band_names=[p.value_attribute or "value"],
        )
        emit("output", core.to_table([desc]))
        self.ctx.info(f"{len(geoms)} entité(s) dessinée(s) dans un raster de {width} x {height} pixels")


def rasterio_bounds(transform: Any, width: int, height: int) -> tuple[float, float, float, float]:
    left, top = transform * (0, 0)
    right, bottom = transform * (width, height)
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)
