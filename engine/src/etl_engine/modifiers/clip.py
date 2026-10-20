"""Clip : découpe des entités par des polygones."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import (
    GEOMETRY_SLICE,
    REJECTED,
    AttributeList,
    ConfigError,
    Emit,
    Modifier,
    Params,
    ordered_map,
    reject,
    slices,
)
from etl_engine.spatial import SpatialIndex, check_same_crs, keep_dimension, overlay, union_groups


class Clip(Modifier):
    name = "Clip"
    label = "Découper"
    category = "Géométrie"
    description = "Découpe les entités « input » par les polygones « overlay » : parties dedans et dehors."
    help = (
        "« union » : les polygones de découpe sont fusionnés ; chaque entité donne au plus une partie « Inside » (dans les "
        "polygones de découpe) et une partie « Outside ». « each » : une partie « Inside » par polygone de découpe qui la recouvre, avec "
        "les attributs choisis du polygone (préfixés) : c'est une intersection avec attributs. Les parties vides sont "
        "supprimées, comme les simples contacts (une parcelle qui touche le polygone de découpe par un bord reste entière dehors). "
        "Une entité entièrement dedans ou dehors sort telle quelle, sans calcul : seules les entités à cheval sont "
        "découpées. Les entités sans géométrie, ou que la découpe n'a pas pu traiter, partent sur « Rejected »."
    )
    mode = "blocking"
    inputs = ("input", "overlay")
    outputs = ("Inside", "Outside")

    class Params(Params):
        mode: Literal["union", "each"] = Field(
            "union",
            title="Polygones de découpe",
            description=(
                "union : les polygones de découpe sont fusionnés (au plus une partie Inside par entité) ; each : une partie "
                "Inside par polygone de découpe qui recouvre l'entité."
            ),
        )
        overlay_attributes: AttributeList = Field(
            [], title="Attributs des polygones à copier", description="Mode « each »."
        )
        prefix: str = Field(
            "overlay_",
            title="Préfixe des attributs copiés",
            description="Ajouté devant le nom des attributs copiés du polygone de découpe (mode each).",
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        a, b = inputs.get("input"), inputs.get("overlay")
        if a is not None and b is not None:
            check_same_crs(a, b)
        inside = a
        if a is not None and b is not None and self.params.mode == "each":
            for name in self.params.overlay_attributes:
                if name in b.names:
                    inside = inside.append(pa.field(self.params.prefix + name, b.field(name).type))
        return {"Inside": inside, "Outside": a}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # les polygones de découpe sont lus, réparés et indexés une fois ; les entités passent par tranches, plusieurs à la fois
        p = self.params
        cutter = collect(inputs.get("overlay", ()))
        index = None
        if cutter is not None and cutter.num_rows:
            if GEOM not in cutter.column_names:
                raise ConfigError("les entités « overlay » n'ont pas de géométrie")
            absent = [a for a in p.overlay_attributes if a not in cutter.column_names]
            if absent:
                raise ConfigError(f"attributs absents des polygones de découpe : {absent}")
            geoms = geometries(cutter)
            broken = np.flatnonzero(~shapely.is_missing(geoms) & ~shapely.is_valid(geoms))
            if len(broken):
                dims = shapely.get_dimensions(geoms[broken])
                geoms[broken] = keep_dimension(shapely.make_valid(geoms[broken]), dims)
                n_broken = len(broken)
                self.ctx.warning(f"{n_broken} polygone(s) de découpe invalide(s) réparé(s)")
            index = SpatialIndex(cutter, geoms)

        def clip(batch: pa.Table) -> tuple[list[tuple[str, pa.Table]], int, int, int]:
            if GEOM not in batch.column_names:
                raise ConfigError("les entités « input » n'ont pas de géométrie")
            if index is not None:
                check_same_crs(batch, cutter)
            return _clip(batch, cutter, index, p.mode, p.overlay_attributes, p.prefix)

        counts = np.zeros(3, dtype=np.int64)
        batches = slices(inputs.get("input", ()), GEOMETRY_SLICE)
        for out, *n in ordered_map(clip, batches, self.ctx.workers, self.ctx):
            for port, table in out:
                emit(port, table)
            counts += n
        n_in, n_out, n_bad = (int(c) for c in counts)
        if index is not None:
            self.ctx.info(f"{n_in} partie(s) dedans, {n_out} dehors")
        if n_bad:
            self.ctx.warning(f"{n_bad} entité(s) que la découpe n'a pas pu traiter, sur « Rejected »")


def _rows(table: pa.Table, rows: np.ndarray, computed: np.ndarray, geoms: np.ndarray, crs: str | None) -> pa.Table:
    """Lignes `rows` de `table` ; là où `computed` est vrai, la géométrie est remplacée par `geoms` (dans l'ordre).

    Les autres lignes gardent leur géométrie d'origine, octet pour octet : rien n'est recalculé ni réencodé.
    """
    out = table.take(pa.array(rows, pa.int64()))
    if computed.any():
        column = out.column(GEOM).combine_chunks()
        new = pa.array(shapely.to_wkb(geoms), column.type)
        out = out.set_column(out.column_names.index(GEOM), GEOM, pc.replace_with_mask(column, pa.array(computed), new))
    return with_crs(out, crs)


def _clip(
    batch: pa.Table, cutter: pa.Table | None, index: SpatialIndex | None, mode: str, attributes: list[str], prefix: str
) -> tuple[list[tuple[str, pa.Table]], int, int, int]:
    """Parties dedans et dehors d'une tranche d'entités : (sorties, nombre dedans, nombre dehors, nombre rejeté).

    Une entité entièrement dans un polygone sort telle quelle dedans ; une entité qui ne touche aucun polygone sort
    telle quelle dehors ; seules les autres sont découpées, contre la partie locale des polygones qu'elles touchent.
    """
    a = geometries(batch)
    crs = get_crs(batch)
    n = len(a)
    missing = shapely.is_missing(a)
    touched = np.zeros(n, dtype=bool)
    covered = np.zeros(n, dtype=bool)
    ai = bj = np.zeros(0, dtype=np.int64)
    within = np.zeros(0, dtype=bool)
    if index is not None:
        ai, bj = index.pairs(a, "intersects")
        within = index.test(a, ai, bj, "covered_by")  # entité entièrement dans ce polygone : rien à calculer
        touched[ai] = True
        covered[ai[within]] = True
    dims = shapely.get_dimensions(a)
    errors: dict[int, str] = {}

    def failed(rows: np.ndarray, found: dict[int, str]) -> None:
        for k, message in found.items():
            errors.setdefault(int(rows[k]), message)

    # entités à cheval : leurs polygones locaux ; réunis par entité (ai est trié) pour celles qu'aucun ne couvre
    edge = ~within
    ei, ej = ai[edge], bj[edge]
    local = index.local(ej, shapely.bounds(a[ei])) if index is not None else a[:0]
    open_ = ~covered[ei]
    rows, starts = np.unique(ei[open_], return_index=True)
    covers, found = union_groups(local[open_], starts)
    failed(rows, found)
    if mode == "union":
        pieces, found = overlay(shapely.intersection, a[rows], covers)
        failed(rows, found)
    else:
        pieces, found = overlay(shapely.intersection, a[ei], local)
        failed(ei, found)
    rests, found = overlay(shapely.difference, a[rows], covers)
    failed(rows, found)
    bad = np.zeros(n, dtype=bool)
    bad[list(errors)] = True
    out: list[tuple[str, pa.Table]] = []

    # dedans : telle quelle si couverte, sa partie dedans si elle est à cheval
    if mode == "union":
        pieces = keep_dimension(pieces, dims[rows])
        piece_of = np.full(n, None, dtype=object)
        piece_of[rows] = pieces
        cut = np.zeros(n, dtype=bool)
        cut[rows[~shapely.is_missing(pieces)]] = True
        inside = np.flatnonzero((covered | cut) & ~bad)
        has_inside = covered | cut
        computed = ~covered[inside]
        if len(inside):
            out.append(("Inside", _rows(batch, inside, computed, piece_of[inside[computed]], crs)))
        n_inside = len(inside)
    else:
        piece = np.full(len(ai), None, dtype=object)
        piece[edge] = keep_dimension(pieces, dims[ei])
        keep = (within | ~shapely.is_missing(piece)) & ~bad[ai]
        has_inside = np.zeros(n, dtype=bool)
        has_inside[ai[within | ~shapely.is_missing(piece)]] = True
        computed = ~within[keep]
        if keep.any():
            inside_table = _rows(batch, ai[keep], computed, piece[keep][computed], crs)
            taken = pa.array(bj[keep], pa.int64())
            for name in attributes:
                inside_table = inside_table.append_column(prefix + name, cutter.column(name).take(taken))
            out.append(("Inside", inside_table))
        n_inside = int(keep.sum())

    # dehors : entière si elle ne touche rien (ou par un simple contact), son reste si elle est à cheval, rien si elle
    # est couverte
    rests = keep_dimension(rests, dims[rows])
    rest_of = np.full(n, None, dtype=object)
    rest_of[rows] = rests
    has_rest = np.zeros(n, dtype=bool)
    has_rest[rows[~shapely.is_missing(rests)]] = True
    contact = touched & ~covered & ~has_inside
    outside = np.flatnonzero(~missing & ~bad & (~touched | contact | (has_rest & ~covered)))
    computed = touched[outside] & ~contact[outside]
    if len(outside):
        out.append(("Outside", _rows(batch, outside, computed, rest_of[outside[computed]], crs)))

    rejected = []
    if missing.any():
        rejected.append(reject(batch.filter(pa.array(missing)), "pas de géométrie"))
    if errors:
        wrong = np.array(sorted(errors), dtype=np.int64)
        reasons = pa.array([f"découpe impossible : {errors[int(r)]}" for r in wrong], pa.string())
        rejected.append(reject(batch.take(pa.array(wrong, pa.int64())), reasons))
    out += [(REJECTED, r) for r in rejected]
    return out, n_inside, len(outside), sum(r.num_rows for r in rejected)
