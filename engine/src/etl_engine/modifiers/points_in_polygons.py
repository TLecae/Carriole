"""PointsInPolygons : attributs du polygone qui contient chaque point."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import (
    REJECTED,
    AttributeList,
    ConfigError,
    Emit,
    Modifier,
    Params,
    ordered_map,
    reject,
)
from etl_engine.spatial import SpatialIndex, check_same_crs


class PointsInPolygons(Modifier):
    name = "PointsInPolygons"
    label = "Point dans polygone"
    category = "Jointures"
    description = "Ajoute à chaque point les attributs du polygone qui le contient (jointure spatiale)."
    help = (
        "Les points dans un polygone sortent sur « Inside » avec les attributs choisis du polygone (tous par "
        "défaut), les autres sur « Outside ». Si plusieurs polygones contiennent le point : « first » garde le "
        "premier, « all » produit une entité par polygone, « error » refuse. Un point sur la frontière "
        "compte comme dedans. Les entités sans géométrie partent sur « Rejected »."
    )
    mode = "blocking"
    inputs = ("points", "areas")
    outputs = ("Inside", "Outside")

    class Params(Params):
        attributes: AttributeList = Field(
            [], title="Attributs du polygone à copier", description="Vide = tous hors géométrie."
        )
        prefix: str = Field(
            "",
            title="Préfixe des attributs copiés",
            description="Ajouté devant le nom des attributs copiés du polygone.",
        )
        on_multiple: Literal["first", "all", "error"] = Field(
            "first",
            title="Plusieurs polygones",
            description=(
                "Si plusieurs polygones contiennent le point — first : le premier ; all : une entité par polygone"
                " ; error : refus."
            ),
        )

    def _attributes(self, areas: pa.Schema) -> list[str]:
        if self.params.attributes:
            absent = [a for a in self.params.attributes if a not in areas.names]
            if absent:
                raise ConfigError(f"attributs absents des polygones : {absent}")
            return list(self.params.attributes)
        return [n for n in areas.names if n != GEOM]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        pts, areas = inputs.get("points"), inputs.get("areas")
        if pts is None or areas is None:
            return {"Inside": None, "Outside": pts}
        check_same_crs(pts, areas)
        out = pts
        for name in self._attributes(areas):
            target = self.params.prefix + name
            field = pa.field(target, areas.field(name).type)
            out = out.set(out.get_field_index(target), field) if target in out.names else out.append(field)
        return {"Inside": out, "Outside": pts}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # les polygones sont lus et indexés une fois ; les points passent lot par lot, plusieurs lots à la fois
        p = self.params
        areas = collect(inputs.get("areas", ()))
        index = None
        if areas is not None and areas.num_rows:
            if GEOM not in areas.column_names:
                raise ConfigError("les entités « areas » n'ont pas de géométrie")
            index = SpatialIndex(areas)
            attrs = self._attributes(areas.schema)

        def overlay(pts: pa.Table) -> list[tuple[str, pa.Table]]:
            if GEOM not in pts.column_names:
                raise ConfigError("les entités « points » n'ont pas de géométrie")
            geoms = geometries(pts)
            missing = shapely.is_missing(geoms)
            out: list[tuple[str, pa.Table]] = []
            if index is None:
                out.append(("Outside", pts.filter(pa.array(~missing))))
            else:
                check_same_crs(pts, areas)
                pi, aj = index.pairs(geoms, "intersects")
                if len(pi) and p.on_multiple != "all":
                    _, first = np.unique(pi, return_index=True)
                    if p.on_multiple == "error" and len(first) != len(pi):
                        raise ConfigError("un point est dans plusieurs polygones (choisir « first » ou « all »)")
                    pi, aj = pi[first], aj[first]
                if len(pi):
                    inside = pts.take(pa.array(pi, pa.int64()))
                    source = areas.select(attrs).take(pa.array(aj, pa.int64()))
                    for name in attrs:
                        target = p.prefix + name
                        column = source.column(name)
                        if target in inside.column_names:
                            inside = inside.set_column(inside.column_names.index(target), target, column)
                        else:
                            inside = inside.append_column(target, column)
                    out.append(("Inside", with_crs(inside, get_crs(pts))))
                outside = np.setdiff1d(np.arange(pts.num_rows), pi)
                outside = outside[~missing[outside]]
                if len(outside):
                    out.append(("Outside", pts.take(pa.array(outside, pa.int64()))))
            if missing.any():
                out.append((REJECTED, reject(pts.filter(pa.array(missing)), "pas de géométrie")))
            return out

        for out in ordered_map(overlay, inputs.get("points", ()), self.ctx.workers, self.ctx):
            for port, table in out:
                emit(port, table)
