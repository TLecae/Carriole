"""CreateGrid : carroyage (carrés ou hexagones) sur une emprise."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import ConfigError, Crs, Emit, Modifier, Params
from etl_engine.spatial import SpatialIndex

MAX_CELLS = 5_000_000


class CreateGrid(Modifier):
    name = "CreateGrid"
    label = "Carroyage"
    category = "Géométrie"
    description = "Génère une grille de carrés ou d'hexagones sur une emprise (saisie, ou celle des entités reçues)."
    help = (
        "L'emprise est saisie (X min, Y min, X max, Y max, dans le système de coordonnées choisi) ou, avec « Emprise des "
        "entités reçues », prise sur les géométries de l'entrée (le nœud a alors une entrée, et la grille reprend leur "
        "système de coordonnées). « Taille » est la largeur d'une cellule, dans l'unité du système (mètres en Lambert-93) : "
        "pour un hexagone, la distance entre deux côtés opposés. Chaque cellule reçoit son numéro (`cell_id`), sa colonne "
        "et sa ligne. Les hexagones débordent légèrement de l'emprise ; « Ne garder que les cellules qui touchent les "
        "entités » écarte les autres. Au plus 5 millions de cellules."
    )
    inputs = ()
    rejected = False

    class Params(Params):
        from_input: bool = Field(
            False, title="Emprise des entités reçues", description="Prend l'emprise sur les géométries de l'entrée."
        )
        xmin: float = Field(0.0, title="X min", description="Bord gauche (emprise saisie).")
        ymin: float = Field(0.0, title="Y min", description="Bord bas.")
        xmax: float = Field(1000.0, title="X max", description="Bord droit.")
        ymax: float = Field(1000.0, title="Y max", description="Bord haut.")
        size: float = Field(
            100.0, gt=0, title="Taille", description="Largeur d'une cellule, dans l'unité du système de coordonnées."
        )
        shape: Literal["square", "hexagon"] = Field("square", title="Forme", description="square ou hexagon.")
        crs: Crs = Field("", title="Système de coordonnées", description="Celui de l'emprise saisie.")
        only_touching: bool = Field(
            False,
            title="Ne garder que les cellules qui touchent les entités",
            description="Avec l'emprise des entités : écarte les cellules qui n'en rencontrent aucune.",
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if params is not None and getattr(params, "from_input", False):
            return ("input",), ("output",)
        return (), ("output",)

    def _extent(self, geoms: np.ndarray | None) -> tuple[float, float, float, float]:
        p = self.params
        if p.from_input:
            if geoms is None or not len(geoms):
                raise ConfigError("aucune géométrie reçue : emprise introuvable")
            xmin, ymin, xmax, ymax = shapely.total_bounds(geoms)
            return float(xmin), float(ymin), float(xmax), float(ymax)
        if p.xmax <= p.xmin or p.ymax <= p.ymin:
            raise ConfigError("l'emprise est vide : X max > X min et Y max > Y min")
        return p.xmin, p.ymin, p.xmax, p.ymax

    def _cells(self, extent: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(géométries, colonnes, lignes) de toutes les cellules."""
        p = self.params
        xmin, ymin, xmax, ymax = extent
        if p.shape == "square":
            cols = math.ceil((xmax - xmin) / p.size)
            rows = math.ceil((ymax - ymin) / p.size)
            total = cols * rows
            self._guard(total)
            c, r = np.meshgrid(np.arange(cols), np.arange(rows))
            c, r = c.ravel(), r.ravel()
            x0, y0 = xmin + c * p.size, ymin + r * p.size
            return shapely.box(x0, y0, x0 + p.size, y0 + p.size), c, r
        radius = p.size / math.sqrt(3)  # rayon du cercle circonscrit (hexagone « pointe en haut »)
        step_y = 1.5 * radius
        rows = math.ceil((ymax - ymin) / step_y) + 2
        cols = math.ceil((xmax - xmin) / p.size) + 2
        self._guard(cols * rows)
        # une rangée et une colonne de plus de chaque côté : le pavage recouvre toute l'emprise, bords compris
        c, r = np.meshgrid(np.arange(-1, cols - 1), np.arange(-1, rows - 1))
        c, r = c.ravel(), r.ravel()
        cx = xmin + c * p.size + (r % 2) * (p.size / 2)
        cy = ymin + r * step_y
        angles = np.radians(np.arange(6) * 60 + 30)
        xs = cx[:, None] + radius * np.cos(angles)[None, :]
        ys = cy[:, None] + radius * np.sin(angles)[None, :]
        cells = shapely.polygons(np.stack([xs, ys], axis=2))
        touching = shapely.intersects(
            cells, shapely.box(xmin, ymin, xmax, ymax)
        )  # écarte celles qui sont tout à fait dehors
        return cells[touching], (c + 1)[touching], (r + 1)[touching]

    @staticmethod
    def _guard(total: int) -> None:
        if total > MAX_CELLS:
            raise ConfigError(
                f"trop de cellules : {total} (maximum {MAX_CELLS}) ; agrandir la taille ou réduire l'emprise"
            )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        if not self.params.from_input:
            self._extent(None)
        schema = pa.schema([("cell_id", pa.int64()), ("col", pa.int64()), ("row", pa.int64()), (GEOM, pa.binary())])
        upstream = inputs.get("input")
        crs = get_crs(upstream) if (self.params.from_input and upstream is not None) else (self.params.crs or None)
        return {"output": with_crs(schema.empty_table(), crs).schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        geoms = None
        crs = p.crs or None
        if p.from_input:
            table = collect(inputs.get("input", ()))
            if table is None or table.num_rows == 0 or GEOM not in table.column_names:
                raise ConfigError("aucune géométrie reçue : emprise introuvable")
            geoms = geometries(table)
            geoms = geoms[~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)]
            crs = get_crs(table)
        cells, cols, rows = self._cells(self._extent(geoms))
        if p.from_input and p.only_touching and geoms is not None:
            hit = np.zeros(len(cells), dtype=bool)
            hit[SpatialIndex.of(geoms).pairs(cells, "intersects")[0]] = True  # géométries préparées, pas les cellules
            cells, cols, rows = cells[hit], cols[hit], rows[hit]
        for start in range(0, len(cells), self.ctx.batch_rows):
            self.ctx.check_cancel()
            end = min(start + self.ctx.batch_rows, len(cells))
            out = pa.table(
                {
                    "cell_id": pa.array(np.arange(start, end), pa.int64()),
                    "col": pa.array(cols[start:end], pa.int64()),
                    "row": pa.array(rows[start:end], pa.int64()),
                    GEOM: pa.array(shapely.to_wkb(cells[start:end]), pa.binary()),
                }
            )
            emit("output", with_crs(out, crs))
        self.ctx.info(f"{len(cells)} cellule(s) générée(s)")
