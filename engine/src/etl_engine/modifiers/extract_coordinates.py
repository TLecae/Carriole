"""ExtractCoordinates : coordonnées d'un point caractéristique de la géométrie, dans des attributs."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, present, type_ids, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier, Params


class ExtractCoordinates(Modifier):
    name = "ExtractCoordinates"
    label = "Extraire des coordonnées"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Écrit X, Y (et Z) d'un point caractéristique de la géométrie dans des attributs."
    help = (
        "Point utilisé : le point lui-même, le centre de gravité, un point garanti à l'intérieur de la surface, "
        "le premier ou le dernier sommet, ou le centre de l'emprise. Les coordonnées sont celles du système de "
        "la géométrie. « Emprise » ajoute `préfixe_minx`, `_miny`, `_maxx`, `_maxy`."
    )
    rejected = False

    class Params(Params):
        source: Literal["point", "centroid", "point_on_surface", "start", "end", "bbox_center"] = Field(
            "centroid",
            title="Point",
            description=(
                "Quel point lire : point (le point lui-même), centroid (centre de gravité), point_on_surface "
                "(toujours dedans), start / end (premier / dernier sommet), bbox_center (centre de l'emprise)."
            ),
        )
        prefix: str = Field("", title="Préfixe des attributs", description="Ex. « pt_ » donne pt_x, pt_y.")
        z: bool = Field(False, title="Extraire aussi Z", description="Ajoute aussi l'altitude (z).")
        bounds: bool = Field(
            False, title="Ajouter l'emprise", description="Ajoute l'emprise : minx, miny, maxx, maxy (avec le préfixe)."
        )

    def _names(self) -> list[tuple[str, type]]:
        p = self.params.prefix
        names = [f"{p}x", f"{p}y"] + ([f"{p}z"] if self.params.z else [])
        if self.params.bounds:
            names += [f"{p}minx", f"{p}miny", f"{p}maxx", f"{p}maxy"]
        return [(n, float) for n in names]

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        for name, _ in self._names():
            f = pa.field(name, pa.float64())
            schema = schema.set(schema.get_field_index(name), f) if name in schema.names else schema.append(f)
        return {"output": schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        found = present(geoms)
        pts = np.full(len(geoms), None, dtype=object)
        sub = geoms[found]
        if p.source == "point":
            pts[found] = sub
        elif p.source == "centroid":
            pts[found] = shapely.centroid(sub)
        elif p.source == "point_on_surface":
            pts[found] = shapely.point_on_surface(sub)
        elif p.source in ("start", "end"):
            pts[found] = _end_points(sub, p.source == "start", p.z)
        else:
            pts[found] = shapely.centroid(shapely.envelope(sub))
        ok = type_ids(pts) == 0  # Point
        xs = np.full(len(geoms), np.nan)
        ys, zs = xs.copy(), xs.copy()
        xs[ok], ys[ok] = shapely.get_x(pts[ok]), shapely.get_y(pts[ok])
        if p.z:
            zs[ok] = shapely.get_z(pts[ok])
        columns = {f"{p.prefix}x": xs, f"{p.prefix}y": ys}
        if p.z:
            columns[f"{p.prefix}z"] = zs
        if p.bounds:
            b = np.full((len(geoms), 4), np.nan)
            b[found] = shapely.bounds(sub)
            for k, name in enumerate(("minx", "miny", "maxx", "maxy")):
                columns[f"{p.prefix}{name}"] = b[:, k]
        out = batch
        for name, values in columns.items():
            column = pa.array(values, pa.float64(), mask=np.isnan(values))
            if name in out.column_names:
                out = out.set_column(out.column_names.index(name), name, column)
            else:
                out = out.append_column(name, column)
        return {"output": with_crs(out, get_crs(batch))}


def _end_points(geoms: np.ndarray, first: bool, z: bool) -> np.ndarray:
    """Premier (ou dernier) sommet de chaque géométrie, en point : toutes les coordonnées lues d'un coup."""
    coords, owner = shapely.get_coordinates(geoms, include_z=z, return_index=True)
    starts = np.searchsorted(owner, np.arange(len(geoms)))
    ends = np.searchsorted(owner, np.arange(len(geoms)), side="right") - 1
    out = np.full(len(geoms), None, dtype=object)
    has = ends >= starts
    out[has] = shapely.points(coords[(starts if first else ends)[has]])
    return out
