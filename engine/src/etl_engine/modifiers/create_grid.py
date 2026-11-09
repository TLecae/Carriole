"""CreateGrid : carroyage (carrés ou hexagones) sur une emprise."""

from __future__ import annotations

import math
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import ConfigError, Crs, Emit, Modifier, Params, ordered_map
from etl_engine.spatial import SpatialIndex

CELL_BYTES = 120  # place d'une cellule écrite (WKB, numéro, colonne, ligne), compression comprise


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
        "entités » écarte les autres."
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

    def _layout(self, extent: tuple[float, float, float, float]) -> tuple[int, int]:
        """(colonnes, lignes) de la grille ; un hexagone en a une de plus de chaque côté (le pavage couvre les bords)."""
        p = self.params
        xmin, ymin, xmax, ymax = extent
        if p.shape == "square":
            return math.ceil((xmax - xmin) / p.size), math.ceil((ymax - ymin) / p.size)
        return math.ceil((xmax - xmin) / p.size) + 2, math.ceil((ymax - ymin) / (1.5 * p.size / math.sqrt(3))) + 2

    def _cells(
        self, extent: tuple[float, float, float, float], first: int, stop: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(géométries, colonnes, lignes) des cellules `first` à `stop` (exclue), ligne par ligne de la grille : la
        grille est produite par tranches, sa taille n'est pas bornée par la mémoire."""
        p = self.params
        xmin, ymin, xmax, ymax = extent
        cols, _ = self._layout(extent)
        k = np.arange(first, stop, dtype=np.int64)
        r, c = k // cols, k % cols
        if p.shape == "square":
            x0, y0 = xmin + c * p.size, ymin + r * p.size
            return shapely.box(x0, y0, x0 + p.size, y0 + p.size), c, r
        radius = p.size / math.sqrt(3)  # rayon du cercle circonscrit (hexagone « pointe en haut »)
        c, r = c - 1, r - 1  # une rangée et une colonne de plus de chaque côté
        cx = xmin + c * p.size + (r % 2) * (p.size / 2)
        cy = ymin + r * 1.5 * radius
        angles = np.radians(np.arange(6) * 60 + 30)
        xs = cx[:, None] + radius * np.cos(angles)[None, :]
        ys = cy[:, None] + radius * np.sin(angles)[None, :]
        cells = shapely.polygons(np.stack([xs, ys], axis=2))
        touching = shapely.intersects(cells, shapely.box(xmin, ymin, xmax, ymax))  # écarte celles tout à fait dehors
        return cells[touching], (c + 1)[touching], (r + 1)[touching]

    def _guard(self, total: int) -> None:
        """Grille qui ne tiendrait pas sur le disque du run (taille de cellule trop petite, emprise en degrés…)."""
        folder = self.ctx.work_dir or Path(tempfile.gettempdir())
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        needed = total * CELL_BYTES
        if needed > shutil.disk_usage(folder).free:
            raise ConfigError(
                f"trop de cellules : {total} (environ {needed / 2**30:.0f} Go) ; agrandir la taille ou réduire l'emprise"
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
        extent = self._extent(geoms)
        cols, rows = self._layout(extent)
        total = cols * rows
        touching = p.from_input and p.only_touching and geoms is not None
        if not touching:
            self._guard(total)
        index = SpatialIndex.of(geoms) if touching else None  # géométries préparées, pas les cellules

        def band(first: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            cells, col, row = self._cells(extent, first, min(first + self.ctx.batch_rows, total))
            if index is not None:
                hit = np.zeros(len(cells), dtype=bool)
                hit[index.pairs(cells, "intersects")[0]] = True
                cells, col, row = cells[hit], col[hit], row[hit]
            return cells, col, row

        produced = 0
        firsts = range(0, total, self.ctx.batch_rows)
        for cells, col, row in ordered_map(band, firsts, max(1, self.ctx.workers), self.ctx):
            if not len(cells):
                continue
            out = pa.table(
                {
                    "cell_id": pa.array(np.arange(produced, produced + len(cells)), pa.int64()),
                    "col": pa.array(col, pa.int64()),
                    "row": pa.array(row, pa.int64()),
                    GEOM: pa.array(shapely.to_wkb(cells), pa.binary()),
                }
            )
            emit("output", with_crs(out, crs))
            produced += len(cells)
        self.ctx.info(f"{produced} cellule(s) générée(s)")
