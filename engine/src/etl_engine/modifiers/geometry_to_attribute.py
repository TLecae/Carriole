"""GeometryToAttribute : copie la géométrie dans un attribut texte."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier, Params


class GeometryToAttribute(Modifier):
    name = "GeometryToAttribute"
    label = "Géométrie vers attribut"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Copie la géométrie de chaque entité dans un attribut texte (WKT, GeoJSON ou WKB hexadécimal)."
    help = (
        "Utile pour écrire une géométrie dans un CSV ou une colonne texte. « Retirer la géométrie » enlève "
        "ensuite la géométrie de l'entité. Une entité sans géométrie reçoit un attribut vide. Précision des "
        "coordonnées : laisser vide pour la précision complète."
    )
    rejected = False

    class Params(Params):
        attribute: str = Field(
            "geometry_text", title="Attribut créé", description="Nom de l'attribut texte qui reçoit la géométrie."
        )
        format: Literal["wkt", "geojson", "wkb_hex"] = Field(
            "wkt", title="Format", description="wkt, geojson ou wkb_hex (WKB en hexadécimal)."
        )
        precision: int | None = Field(
            None,
            ge=0,
            le=15,
            title="Décimales (WKT, GeoJSON)",
            description="Décimales conservées dans les coordonnées ; vide = précision complète.",
        )
        remove: bool = Field(
            False,
            title="Retirer la géométrie",
            description="Retire la géométrie de l'entité une fois copiée dans l'attribut.",
        )

    def _check(self) -> None:
        if not self.params.attribute or self.params.attribute == GEOM:
            raise ConfigError("nom d'attribut invalide (« geometry » est réservé)")

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        out = schema
        f = pa.field(self.params.attribute, pa.string())
        out = out.set(out.get_field_index(f.name), f) if f.name in out.names else out.append(f)
        return {"output": out.remove(out.get_field_index(GEOM)) if self.params.remove else out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        digits = p.precision if p.precision is not None else 15
        if p.format == "wkt":
            text = shapely.to_wkt(geoms, rounding_precision=digits, trim=True)
        elif p.format == "geojson":
            text = shapely.to_geojson(shapely.set_precision(geoms, 10**-digits) if p.precision is not None else geoms)
        else:
            text = shapely.to_wkb(geoms, hex=True)
        column = pa.array(text, pa.string())
        out = batch
        if p.attribute in out.column_names:
            out = out.set_column(out.column_names.index(p.attribute), p.attribute, column)
        else:
            out = out.append_column(p.attribute, column)
        if p.remove:
            out = out.drop_columns([GEOM])
        return {"output": with_crs(out, get_crs(batch))}
