"""Hull : remplace la géométrie par son enveloppe (convexe, concave, rectangle, cercle…)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect, group_codes, group_slices
from etl_engine.data import GEOM, geometries, get_crs, set_geometries
from etl_engine.sdk import (
    GEOMETRY_SLICE,
    REJECTED,
    AttributeList,
    ConfigError,
    Emit,
    Modifier,
    Params,
    missing_attributes,
    ordered_map,
    reject,
    require_attributes,
    slices,
)


class Hull(Modifier):
    name = "Hull"
    label = "Enveloppe des géométries"
    category = "Géométrie"
    description = (
        "Remplace la géométrie par son enveloppe : convexe, concave, emprise, rectangle orienté ou cercle englobant."
    )
    help = (
        "« convex » : enveloppe convexe (l'élastique autour des points). « concave » : enveloppe qui épouse mieux la forme ; "
        "« Finesse » va de 1 (convexe) à 0 (très creusée). « envelope » : emprise (rectangle aligné sur les axes). "
        "« rectangle » : rectangle orienté de plus petite surface. « circle » : plus petit cercle englobant. "
        "Portée : « feature » calcule l'enveloppe de chaque entité ; « group » réunit les entités de chaque groupe en une "
        "seule (attributs de la première) ; « all » en fait une seule pour tout. Les entités sans géométrie partent sur "
        "« Rejected »."
    )

    class Params(Params):
        method: Literal["convex", "concave", "envelope", "rectangle", "circle"] = Field(
            "convex", title="Enveloppe", description="convex, concave, envelope, rectangle ou circle."
        )
        scope: Literal["feature", "group", "all"] = Field(
            "feature",
            title="Portée",
            description="feature : une par entité ; group : une par groupe ; all : une pour toutes les entités.",
        )
        group_by: AttributeList = Field(
            default_factory=list, title="Regrouper par", description="Pour la portée « group »."
        )
        ratio: float = Field(0.3, ge=0, le=1, title="Finesse (concave)", description="1 = convexe, 0 = très creusée.")
        allow_holes: bool = Field(
            False, title="Trous autorisés (concave)", description="L'enveloppe concave peut avoir des trous."
        )

    def _check(self) -> None:
        if self.params.scope == "group" and not self.params.group_by:
            raise ConfigError("la portée « group » demande un attribut de regroupement")

    def _hull(self, geoms: np.ndarray) -> np.ndarray:
        p = self.params
        match p.method:
            case "convex":
                return shapely.convex_hull(geoms)
            case "concave":
                return shapely.concave_hull(geoms, ratio=p.ratio, allow_holes=p.allow_holes)
            case "envelope":
                return shapely.envelope(geoms)
            case "rectangle":
                return shapely.minimum_rotated_rectangle(geoms)
            case _:
                return shapely.minimum_bounding_circle(geoms)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if self.params.scope == "group":
            require_attributes(schema, self.params.group_by)
        return {"output": schema}

    def _usable(self, batch: pa.Table) -> tuple[pa.Table, pa.Table | None, np.ndarray]:
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        ok = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
        bad = reject(batch.filter(pa.array(~ok)), "pas de géométrie") if not ok.all() else None
        return batch.filter(pa.array(ok)), bad, geoms[ok]

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        if p.scope == "feature":  # une enveloppe par entité : en tranches, plusieurs à la fois

            def hull(batch: pa.Table) -> tuple[pa.Table | None, pa.Table | None]:
                good, bad, geoms = self._usable(batch)
                return (set_geometries(good, self._hull(geoms), get_crs(batch)) if good.num_rows else None), bad

            batches = slices(inputs.get("input", ()), GEOMETRY_SLICE)
            for out, bad in ordered_map(hull, batches, self.ctx.workers, self.ctx):
                if out is not None:
                    emit("output", out)
                if bad is not None:
                    emit(REJECTED, bad)
            return
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        good, bad, geoms = self._usable(table)
        if bad is not None:
            emit(REJECTED, bad)
        if not good.num_rows:
            return
        if p.scope == "all":
            keys, count = np.zeros(good.num_rows, dtype=np.int64), 1
        else:
            missing = [a for a in p.group_by if a not in good.column_names]
            if missing:
                raise missing_attributes(missing)
            keys, count = group_codes(good, list(p.group_by))
        order, bounds = group_slices(keys, count)
        first = order[bounds[:-1]]
        collected = shapely.geometrycollections(geoms[order], indices=keys[order])
        out = good.take(pa.array(first))
        emit("output", set_geometries(out, self._hull(collected), get_crs(table)))
        self.ctx.info(f"{good.num_rows} entités réunies en {len(first)} enveloppe(s)")
