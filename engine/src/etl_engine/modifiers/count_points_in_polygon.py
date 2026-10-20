"""CountPointsInPolygon : nombre de points (et sommes d'attributs) dans chaque polygone."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries
from etl_engine.sdk import REJECTED, AttributeList, ConfigError, Emit, Modifier, Params, ordered_map, reject
from etl_engine.spatial import SpatialIndex, check_same_crs

EMIT_ROWS = 100_000  # polygones émis par lot


class CountPointsInPolygon(Modifier):
    name = "CountPointsInPolygon"
    label = "Compter les points dans les emprises"
    category = "Jointures"
    description = "Ajoute à chaque polygone le nombre de points qu'il contient, et la somme d'attributs de ces points."
    help = (
        "Chaque polygone (« areas ») sort sur « output » avec le nombre de points (« points ») qu'il contient, 0 s'il "
        "n'en contient aucun, et, si demandé, la somme d'attributs numériques de ces points (« <attribut>_sum »). "
        "Un point sur la frontière compte comme dedans. Les points peuvent être d'autres entités (bâtiments…) : "
        "une entité compte dans chaque polygone qu'elle touche. « Plusieurs polygones » : un point sur la limite "
        "commune de deux polygones compte dans les deux (« all »), ou seulement dans le premier (« first » : la "
        "somme des comptes égale alors le nombre de points). Les polygones sans géométrie partent sur « Rejected » ; "
        "les points sans géométrie ne comptent nulle part."
    )
    mode = "blocking"
    inputs = ("areas", "points")
    outputs = ("output",)

    class Params(Params):
        count_attribute: str = Field(
            "_count",
            title="Attribut du nombre de points",
            description="Ajouté à chaque polygone (remplacé s'il existe).",
        )
        sum_attributes: AttributeList = Field(
            [],
            title="Attributs des points à additionner",
            description="Attributs numériques des points : leur somme par polygone dans « <attribut>_sum ».",
        )
        on_multiple: Literal["all", "first"] = Field(
            "all",
            title="Plusieurs polygones",
            description="Un point dans plusieurs polygones — all : compte dans chacun ; first : dans le premier seulement.",
        )

    def _added(self) -> list[tuple[str, pa.DataType]]:
        p = self.params
        return [(p.count_attribute, pa.int64()), *((f"{name}_sum", pa.float64()) for name in p.sum_attributes)]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        areas, pts = inputs.get("areas"), inputs.get("points")
        if areas is None:
            return {"output": None}
        if pts is not None:
            check_same_crs(pts, areas)
            self._check_sums(pts)
        out = areas
        for name, kind in self._added():
            field = pa.field(name, kind)
            out = out.set(out.get_field_index(name), field) if name in out.names else out.append(field)
        return {"output": out}

    def _check_sums(self, pts: pa.Schema) -> None:
        absent = [a for a in self.params.sum_attributes if a not in pts.names]
        if absent:
            raise ConfigError(f"attributs absents des points : {absent}")
        for name in self.params.sum_attributes:
            kind = pts.field(name).type
            if not (pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_decimal(kind)):
                raise ConfigError(f"« {name} » n'est pas numérique : impossible de l'additionner")

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # les polygones sont lus et indexés une fois ; les points passent lot par lot, plusieurs lots à la fois, et
        # ne laissent que leurs comptes (jamais gardés en mémoire)
        p = self.params
        areas = collect(inputs.get("areas", ()))
        if areas is None or areas.num_rows == 0:
            for _ in inputs.get("points", ()):  # rien à compter : les points sont seulement consommés
                pass
            return
        if GEOM not in areas.column_names:
            raise ConfigError("les entités « areas » n'ont pas de géométrie")
        index = SpatialIndex(areas)
        n = areas.num_rows
        sums = p.sum_attributes

        def count(pts: pa.Table) -> tuple[np.ndarray, np.ndarray, int]:
            if GEOM not in pts.column_names:
                raise ConfigError("les entités « points » n'ont pas de géométrie")
            check_same_crs(pts, areas)
            self._check_sums(pts.schema)
            pi, aj = index.pairs(geometries(pts), "intersects")
            if p.on_multiple == "first" and len(pi):
                _, first = np.unique(pi, return_index=True)
                pi, aj = pi[first], aj[first]
            counts = np.bincount(aj, minlength=n).astype(np.int64)
            totals = np.zeros((len(sums), n))
            for k, name in enumerate(sums):
                values = pc.fill_null(pts.column(name).cast(pa.float64()), 0.0).to_numpy(zero_copy_only=False)
                totals[k] = np.bincount(aj, weights=values[pi], minlength=n)
            return counts, totals, pts.num_rows

        counts = np.zeros(n, np.int64)
        totals = np.zeros((len(sums), n))
        points = 0
        for batch_counts, batch_totals, rows in ordered_map(
            count, inputs.get("points", ()), self.ctx.workers, self.ctx
        ):
            counts += batch_counts
            totals += batch_totals
            points += rows

        missing = shapely.is_missing(geometries(areas))
        out = areas
        for (name, kind), values in zip(self._added(), [counts, *totals], strict=True):
            column = pa.array(values, kind)
            out = (
                out.set_column(out.column_names.index(name), name, column)
                if name in out.column_names
                else out.append_column(name, column)
            )
        out = out.replace_schema_metadata(areas.schema.metadata)
        kept = out.filter(pa.array(~missing)) if missing.any() else out
        for start in range(0, kept.num_rows, EMIT_ROWS):
            emit("output", kept.slice(start, EMIT_ROWS))
        if missing.any():
            emit(REJECTED, reject(areas.filter(pa.array(missing)), "pas de géométrie"))
        self.ctx.info(f"{points} points comptés dans {kept.num_rows} polygones")
