"""GeometryFromAttribute : construit la géométrie depuis un attribut texte (WKT, GeoJSON, WKB hexadécimal)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, Attribute, ConfigError, Crs, Modifier, Params, reject


class GeometryFromAttribute(Modifier):
    name = "GeometryFromAttribute"
    label = "Attribut vers géométrie"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = (
        "Construit la géométrie de chaque entité à partir d'un attribut texte (WKT, GeoJSON, WKB hexadécimal)."
    )
    help = (
        "Une valeur vide donne une entité sans géométrie ; une valeur illisible envoie l'entité sur « Rejected » "
        "avec le motif. Le système de coordonnées est celui indiqué (ou celui des données)."
    )

    class Params(Params):
        attribute: Attribute = Field(
            "", title="Attribut contenant la géométrie", description="Attribut texte qui contient la géométrie."
        )
        format: Literal["wkt", "geojson", "wkb_hex"] = Field(
            "wkt", title="Format", description="Format du texte : wkt, geojson ou wkb_hex (WKB en hexadécimal)."
        )
        crs: Crs = Field(
            "",
            title="Système de coordonnées",
            description="Système de coordonnées de la géométrie lue ; vide = celui des données.",
            examples=["EPSG:2154"],
        )
        remove_attribute: bool = Field(
            False, title="Retirer l'attribut texte", description="Retire l'attribut texte une fois la géométrie créée."
        )

    def _check(self) -> None:
        if not self.params.attribute:
            raise ConfigError("attribut non renseigné")

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        out = schema if GEOM in schema.names else schema.append(pa.field(GEOM, pa.binary()))
        if self.params.remove_attribute:
            out = out.remove(out.get_field_index(self.params.attribute))
        return {"output": with_crs(out, self.params.crs) if self.params.crs else out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        if p.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.attribute}")
        texts = pc.utf8_trim_whitespace(batch.column(p.attribute).cast(pa.string()))
        values = np.asarray(texts.to_numpy(zero_copy_only=False), dtype=object)
        given = ~pc.is_null(texts).to_numpy(zero_copy_only=False)
        given[given] = values[given] != ""
        geoms = np.full(len(values), None, dtype=object)
        geoms[given] = _parse(values[given], p.format)  # vectorisé ; une valeur illisible donne None
        bad, reasons = [], {}
        for i in np.flatnonzero(given & shapely.is_missing(geoms)):  # les illisibles seulement : leur message
            try:
                _parse(np.array([values[i]], dtype=object), p.format, strict=True)
                message = "valeur vide"
            except Exception as exc:  # noqa: BLE001 - shapely lève des erreurs variées (GEOS, JSON, valeur)
                message = str(exc).splitlines()[0][:100]
            bad.append(int(i))
            reasons[int(i)] = f"géométrie illisible ({p.format}) : {message}"
        column = pa.array(shapely.to_wkb(geoms), pa.binary())
        out = batch
        if GEOM in out.column_names:
            out = out.set_column(out.column_names.index(GEOM), GEOM, column)
        else:
            out = out.append_column(GEOM, column)
        if p.remove_attribute:
            out = out.drop_columns([p.attribute])
        crs = p.crs or get_crs(batch)
        if not bad:
            return {"output": with_crs(out, crs)}
        bad_set = set(bad)
        keep = pa.array([i not in bad_set for i in range(batch.num_rows)])
        return {
            "output": with_crs(out.filter(keep), crs),
            REJECTED: reject(batch.take(pa.array(bad)), pa.array([reasons[i] for i in bad], pa.string())),
        }


def _parse(values: np.ndarray, fmt: str, strict: bool = False) -> np.ndarray:
    """Textes → géométries, d'un coup ; `strict` : lève l'erreur au lieu de rendre None (pour son message)."""
    invalid = "raise" if strict else "ignore"
    if fmt == "wkt":
        return shapely.from_wkt(values, on_invalid=invalid)
    if fmt == "geojson":
        return shapely.from_geojson(values, on_invalid=invalid)
    return shapely.from_wkb(values, on_invalid=invalid)
