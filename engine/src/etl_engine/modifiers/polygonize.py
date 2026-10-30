"""Polygonize : vectorise un raster (classes d'occupation du sol, seuils) en polygones."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import rasterio
import shapely
from pydantic import Field
from rasterio.features import shapes

from etl_engine.data import GEOM, set_geometries
from etl_engine.raster import core
from etl_engine.sdk import Modifier, Params

MAX_PIXELS = 60_000_000


class Polygonize(Modifier):
    name = "Polygonize"
    label = "Raster vers polygones"
    category = "Rasters"
    description = "Transforme un raster en polygones : un polygone par ensemble de pixels voisins de même valeur."
    help = (
        "Pour un raster de classes (occupation du sol, résultat d'un seuil ou d'un reclassement), chaque zone d'un seul "
        "tenant de même valeur devient un polygone avec sa valeur (attribut « Attribut de la valeur ») et le numéro de "
        "la bande. « Connexité » : 4 (pixels qui se touchent par un côté) ou 8 (aussi par un coin). Les pixels sans donnée "
        "ne donnent rien. « Arrondir » regroupe les valeurs décimales proches (1 = au dixième) ; « Surface minimale » "
        "écarte les polygones plus petits (dans l'unité du système au carré). Le raster entier est lu en mémoire "
        "(au plus 60 millions de pixels : découper ou réduire la résolution au-delà). Les attributs du raster sont recopiés."
    )
    rejected = False

    class Params(Params):
        band: int = Field(1, ge=1, title="Bande", description="Bande à vectoriser (à partir de 1).")
        value_attribute: str = Field("value", title="Attribut de la valeur", description="Reçoit la valeur des pixels.")
        connectivity: Literal[4, 8] = Field(4, title="Connexité", description="4 ou 8 voisins.")
        round_decimals: int | None = Field(
            None,
            ge=0,
            le=6,
            title="Arrondir",
            description="Décimales conservées avant de regrouper ; vide = valeurs exactes.",
        )
        min_area: float = Field(0.0, ge=0, title="Surface minimale", description="Ignore les polygones plus petits.")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        core.check_raster_schema(schema)
        extra = [pa.field("band", pa.int64()), pa.field(self.params.value_attribute, pa.float64())]
        others = [f for f in (schema or []) if f.name not in ("raster", GEOM)]
        return {"output": pa.schema([*others, *extra, pa.field(GEOM, pa.binary())])}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        descs = core.descriptors(batch)
        attributes = core.attributes_of(batch)
        pieces: list[pa.Table] = []
        for r, desc in enumerate(descs):
            self.ctx.check_cancel()
            if desc["width"] * desc["height"] > MAX_PIXELS:
                raise_too_big(desc)
            with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
                data = ds.read(p.band, masked=True)
                transform = ds.transform
            mask = ~np.ma.getmaskarray(data)
            values = np.ma.filled(data, 0)
            if p.round_decimals is not None:
                values = np.round(values.astype("float64"), p.round_decimals)
            values = values.astype("float32" if values.dtype.kind == "f" else values.dtype)
            found = [
                (shapely.geometry.shape(g), float(v))
                for g, v in shapes(values, mask=mask, transform=transform, connectivity=p.connectivity)
            ]
            if p.min_area:
                found = [(g, v) for g, v in found if g.area >= p.min_area]
            if not found:
                continue
            n = len(found)
            table = pa.table({"_row": pa.array(range(n), pa.int64())})
            for k, v in attributes.items():
                table = table.append_column(k, pa.repeat(v[r], n))
            table = table.drop_columns(["_row"])
            table = table.append_column("band", pa.repeat(pa.scalar(p.band, pa.int64()), n))
            table = table.append_column(p.value_attribute, pa.array([v for _, v in found], pa.float64()))
            table = table.append_column(GEOM, pa.nulls(n, pa.binary()))
            pieces.append(set_geometries(table, np.array([g for g, _ in found], dtype=object), desc["crs"]))
        if not pieces:
            return {}
        return {"output": pa.concat_tables(pieces, promote_options="default")}


def raise_too_big(desc: dict[str, Any]) -> None:
    from etl_engine.sdk import ConfigError

    raise ConfigError(
        f"raster trop grand pour être vectorisé en une fois ({desc['width']} x {desc['height']} pixels) : "
        "le découper ou réduire sa résolution d'abord"
    )
