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

DETAILED = 100  # valeurs illisibles différentes dont le motif donne l'erreur exacte, par lot ; les autres : le format


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
        "avec le motif. Le système de coordonnées est celui indiqué (ou celui des données). Pour des coordonnées X et Y "
        "dans deux attributs : « Créer un point / un sommet »."
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
        given = pc.fill_null(pc.not_equal(texts, ""), False).to_numpy(zero_copy_only=False)
        geoms = np.full(batch.num_rows, None, dtype=object)
        geoms[given] = _parse(texts.filter(given).to_numpy(zero_copy_only=False), p.format)  # illisible : None
        bad = given & shapely.is_missing(geoms)
        out = batch.filter(pa.array(~bad)) if bad.any() else batch
        column = pa.array(shapely.to_wkb(geoms[~bad]), pa.binary())  # les seules entités gardées
        if GEOM in out.column_names:
            out = out.set_column(out.column_names.index(GEOM), GEOM, column)
        else:
            out = out.append_column(GEOM, column)
        if p.remove_attribute:
            out = out.drop_columns([p.attribute])
        out = with_crs(out, p.crs or get_crs(batch))
        if not bad.any():
            return {"output": out}
        rejected = batch.filter(pa.array(bad))
        return {"output": out, REJECTED: reject(rejected, _reasons(rejected.column(p.attribute), p.format))}


def _reasons(values: pa.ChunkedArray, fmt: str) -> pa.Array:
    """Motif de chaque valeur illisible : l'erreur exacte pour les `DETAILED` premières valeurs différentes, le format
    seul pour les autres (une colonne entière illisible ne coûte pas une analyse par entité)."""
    texts = pc.utf8_trim_whitespace(values.cast(pa.string())).combine_chunks()
    distinct = pc.unique(texts)
    detail = {}
    for value in distinct.slice(0, DETAILED).to_pylist():
        try:
            _parse(np.array([value], dtype=object), fmt, strict=True)
            message = "valeur invalide"
        except Exception as exc:  # noqa: BLE001 - shapely lève des erreurs variées (GEOS, JSON, valeur)
            message = str(exc).splitlines()[0][:100]
        detail[value] = f"géométrie illisible ({fmt}) : {message}"
    known = pa.array(list(detail), pa.string())
    found = pc.index_in(texts, value_set=known)
    reasons = pc.take(pa.array(list(detail.values()), pa.string()), found)
    return pc.fill_null(reasons, f"géométrie illisible ({fmt})")


def _parse(values: np.ndarray, fmt: str, strict: bool = False) -> np.ndarray:
    """Textes → géométries, d'un coup ; `strict` : lève l'erreur au lieu de rendre None (pour son message)."""
    invalid = "raise" if strict else "ignore"
    if fmt == "wkt":
        return shapely.from_wkt(values, on_invalid=invalid)
    if fmt == "geojson":
        return shapely.from_geojson(values, on_invalid=invalid)
    return shapely.from_wkb(values, on_invalid=invalid)
