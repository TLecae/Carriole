"""PointFromXY : crée un point à partir de coordonnées, ou ajoute un sommet à une ligne."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, type_ids, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, Attribute, ConfigError, Crs, Modifier, Params, reject


class PointFromXY(Modifier):
    name = "PointFromXY"
    label = "Créer un point / un sommet"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Crée un point depuis des attributs X/Y (Z), ou ajoute ce point comme sommet d'une ligne."
    help = (
        "« point » : la géométrie devient le point (X, Y[, Z]). « append » : le point est ajouté à la fin de la "
        "ligne existante (une entité sans géométrie reçoit un simple point). Une coordonnée vide ou non numérique "
        "envoie l'entité sur « Rejected ». Le système de coordonnées est celui indiqué (ou celui des données)."
    )

    class Params(Params):
        x: Attribute = Field(
            "", title="Attribut X (ou longitude)", description="Attribut contenant X (ou la longitude)."
        )
        y: Attribute = Field("", title="Attribut Y (ou latitude)", description="Attribut contenant Y (ou la latitude).")
        z: Attribute = Field("", title="Attribut Z", description="Facultatif.")
        mode: Literal["point", "append"] = Field(
            "point",
            title="Action",
            description=(
                "point : la géométrie devient le point ; append : le point est ajouté à la fin de la ligne existante."
            ),
        )
        crs: Crs = Field("", title="Système de coordonnées", description="Vide = celui des données.")

    def _check(self) -> None:
        if not self.params.x or not self.params.y:
            raise ConfigError("attributs X et Y non renseignés")

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            schema = schema.append(pa.field(GEOM, pa.binary()))
        return {"output": with_crs(schema, self.params.crs) if self.params.crs else schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        names = [p.x, p.y, *([p.z] if p.z else [])]
        missing = [n for n in names if n not in batch.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {missing}")
        coords = []
        for name in names:
            try:
                coords.append(batch.column(name).cast(pa.float64(), safe=False).to_numpy(zero_copy_only=False))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                coords.append(np.array([_number(v) for v in batch.column(name).to_pylist()], dtype=float))
        stacked = np.column_stack(coords)
        bad = ~np.isfinite(stacked).all(axis=1)
        out_geoms = np.full(batch.num_rows, None, dtype=object)
        good = ~bad
        out_geoms[good] = shapely.points(stacked[good])
        if p.mode == "append" and GEOM in batch.column_names:
            old = geometries(batch)
            ids = type_ids(old)
            for kind in (1, 0):  # LineString : un sommet de plus ; Point : une ligne de deux sommets
                rows = np.flatnonzero(good & (ids == kind) & ~shapely.is_empty(old))
                if len(rows):
                    out_geoms[rows] = _append(old[rows], stacked[rows])
        wkb = pa.array(shapely.to_wkb(out_geoms), pa.binary())
        out = batch
        if GEOM in out.column_names:
            out = out.set_column(out.column_names.index(GEOM), GEOM, wkb)
        else:
            out = out.append_column(GEOM, wkb)
        keep = pa.array(~bad)
        result = {"output": with_crs(out.filter(keep), p.crs or get_crs(batch))}
        if bad.any():
            result[REJECTED] = reject(batch.filter(pa.array(bad)), "coordonnée vide ou non numérique")
        return result


def _number(value) -> float:
    try:
        return float(str(value).replace(",", ".")) if value is not None else float("nan")
    except ValueError:
        return float("nan")


def _append(lines: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Chaque ligne (ou point) prolongée du point correspondant, d'un coup. En 3D seulement si la ligne et le point
    le sont tous deux ; sinon le Z est laissé de côté."""
    z = points.shape[1] == 3 and bool(shapely.has_z(lines).all())
    coords, owner = shapely.get_coordinates(lines, include_z=z, return_index=True)
    added = points[:, : 3 if z else 2]
    allc = np.concatenate([coords, added])
    alli = np.concatenate([owner, np.arange(len(lines))])
    order = np.argsort(alli, kind="stable")  # les sommets de chaque ligne, puis le nouveau
    return shapely.linestrings(allc[order], indices=alli[order])
