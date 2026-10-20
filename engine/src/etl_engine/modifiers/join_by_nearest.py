"""JoinByNearest : pour chaque entité, ses plus proches voisines parmi d'autres entités."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
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
from etl_engine.spatial import SpatialIndex, check_same_crs


def nearest(
    index: SpatialIndex, geoms: np.ndarray, k: int, max_distance: float | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(indices des bases, indices des voisines, distances) : au plus `k` voisines par base, de la plus proche à la plus
    éloignée. Exact pour tout type de géométrie (distance entre géométries, pas entre centres)."""
    tree = index.tree
    if k == 1:
        pairs, dist = tree.query_nearest(geoms, max_distance=max_distance, return_distance=True, all_matches=False)
        return pairs[0], pairs[1], dist
    # k > 1 : par tours, pour toutes les bases à la fois ; le rayon de recherche d'une base part de 1,5 fois la distance
    # de sa plus proche voisine et double jusqu'à trouver k voisines (ou atteindre la distance maximale)
    first, first_d = tree.query_nearest(geoms, max_distance=max_distance, return_distance=True, all_matches=False)
    bases = first[0]
    radius = np.maximum(first_d * 1.5, 1e-9)
    wanted = min(k, len(index))
    todo = np.arange(len(bases))
    found: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    while len(todo):
        limit = radius[todo] if max_distance is None else np.minimum(radius[todo], max_distance)
        g = geoms[bases[todo]]
        b = shapely.bounds(g)
        qi, qj = tree.query(shapely.box(b[:, 0] - limit, b[:, 1] - limit, b[:, 2] + limit, b[:, 3] + limit))
        ok = index.test(g, qi, qj, "dwithin", limit[qi])
        qi, qj = qi[ok], qj[ok]
        done = np.bincount(qi, minlength=len(todo)) >= wanted
        if max_distance is not None:
            done |= radius[todo] >= max_distance
        done |= limit > 1e12
        sel = done[qi]
        if sel.any():
            qi, qj = qi[sel], qj[sel]
            dist = shapely.distance(g[qi], index.geoms[qj])
            order = np.lexsort((qj, dist, qi))  # par base, de la plus proche à la plus éloignée
            qi, qj, dist = qi[order], qj[order], dist[order]
            starts = np.r_[0, np.flatnonzero(np.diff(qi)) + 1]
            rank = np.arange(len(qi)) - np.repeat(starts, np.diff(np.r_[starts, len(qi)]))
            keep = rank < k
            found.append((bases[todo[qi[keep]]], qj[keep], dist[keep]))
        radius[todo[~done]] *= 2
        todo = todo[~done]
    if not found:
        empty = np.array([], dtype=np.int64)
        return empty, empty, np.array([], dtype=float)
    b_out, c_out, d_out = (np.concatenate(x) for x in zip(*found, strict=True))
    order = np.argsort(b_out, kind="stable")  # bases dans l'ordre, chacune avec ses voisines déjà triées
    return b_out[order], c_out[order], d_out[order]


class JoinByNearest(Modifier):
    name = "JoinByNearest"
    label = "Plus proches voisines"
    category = "Jointures"
    description = "Pour chaque entité « base », trouve ses k plus proches entités parmi les « candidate » et recopie leurs attributs."
    help = (
        "Chaque « base » donne au plus k entités sur « Matched » (une par voisine, de la plus proche à la plus éloignée) : "
        "les attributs de la base, ceux de la voisine (préfixés), son rang (`_rank`, de 1) et la distance (`_distance`, dans "
        "l'unité du système de coordonnées). Une base sans voisine à moins de « Distance maximale » sort sur « NoNeighbor ». "
        "La distance est celle qui sépare les géométries (le bord à bord pour des polygones), pas celle des centres. "
        "Les deux entrées doivent avoir le même système de coordonnées ; une entité sans géométrie part sur « Rejected »."
    )
    inputs = ("base", "candidate")
    outputs = ("Matched", "NoNeighbor")

    class Params(Params):
        count: int = Field(
            1, ge=1, le=100, title="Nombre de voisines (k)", description="Combien de plus proches voisines chercher."
        )
        max_distance: float = Field(
            0.0,
            ge=0,
            title="Distance maximale",
            description="Dans l'unité du système de coordonnées ; 0 = sans limite.",
        )
        attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs de la voisine",
            description="Vide = tous ceux de « candidate » hors géométrie.",
        )
        prefix: str = Field("near_", title="Préfixe", description="Ajouté devant les attributs de la voisine.")
        rank_attribute: str = Field("_rank", title="Attribut du rang", description="Rang de la voisine, de 1.")
        distance_attribute: str = Field(
            "_distance", title="Attribut de la distance", description="Distance à la voisine."
        )

    def _added(self, candidate: pa.Schema) -> list[pa.Field]:
        p = self.params
        names = p.attributes or [n for n in candidate.names if n != GEOM]
        missing = [n for n in names if n not in candidate.names]
        if missing:
            raise ConfigError(f"attributs absents de « candidate » : {', '.join(missing)}")
        fields = [pa.field(p.prefix + n, candidate.field(n).type) for n in names]
        if p.rank_attribute:
            fields.append(pa.field(p.rank_attribute, pa.int64()))
        if p.distance_attribute:
            fields.append(pa.field(p.distance_attribute, pa.float64()))
        return fields

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        base, cand = inputs.get("base"), inputs.get("candidate")
        if base is None or cand is None:
            return {"Matched": None, "NoNeighbor": base}
        for what, schema in (("base", base), ("candidate", cand)):
            if GEOM not in schema.names:
                raise ConfigError(f"les entités « {what} » n'ont pas de géométrie")
        check_same_crs(base, cand, "les entrées base et candidate")
        fields = self._added(cand)
        clash = [f.name for f in fields if f.name in base.names]
        if clash:
            raise ConfigError(f"noms déjà présents dans « base » : {', '.join(clash)} (changer le préfixe)")
        return {"Matched": pa.schema([*base, *fields], metadata=base.metadata), "NoNeighbor": base}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        cand = collect(inputs.get("candidate", ()))
        if cand is None or cand.num_rows == 0 or GEOM not in cand.column_names:
            for batch in inputs.get("base", ()):
                emit("NoNeighbor", batch)
            return
        fields = self._added(cand.schema)
        cgeoms = geometries(cand)
        usable = ~shapely.is_missing(cgeoms) & ~shapely.is_empty(cgeoms)
        index = np.flatnonzero(usable)
        near = SpatialIndex.of(cgeoms[usable])
        names = p.attributes or [n for n in cand.column_names if n != GEOM]
        attrs = {n: cand.column(n) for n in names}
        limit = p.max_distance or None

        def find(batch: pa.Table) -> list[tuple[str, pa.Table]]:
            if GEOM not in batch.column_names:
                raise ConfigError("les entités « base » n'ont pas de géométrie")
            check_same_crs(batch, cand, "les entrées base et candidate")
            clash = [f.name for f in fields if f.name in batch.column_names]
            if clash:
                raise ConfigError(f"noms déjà présents dans « base » : {', '.join(clash)} (changer le préfixe)")
            out: list[tuple[str, pa.Table]] = []
            geoms = geometries(batch)
            ok = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
            if not ok.all():
                out.append((REJECTED, reject(batch.filter(pa.array(~ok)), "pas de géométrie")))
            positions = np.flatnonzero(ok)
            b, c, d = (
                nearest(near, geoms[ok], p.count, limit)
                if len(positions) and len(near)
                else (np.array([], dtype=np.int64),) * 2 + (np.array([]),)
            )
            matched = np.zeros(len(batch), dtype=bool)
            if len(b):
                rows = positions[b]
                matched[rows] = True
                table = batch.take(pa.array(rows))
                taken = pa.array(index[c])
                for n in names:
                    table = table.append_column(p.prefix + n, attrs[n].take(taken))
                if p.rank_attribute:
                    table = table.append_column(p.rank_attribute, pa.array(_ranks(rows), pa.int64()))
                if p.distance_attribute:
                    table = table.append_column(p.distance_attribute, pa.array(d, pa.float64()))
                out.append(("Matched", with_crs(table, get_crs(batch))))
            alone = ok & ~matched
            if alone.any():
                out.append(("NoNeighbor", batch.filter(pa.array(alone))))
            return out

        for out in ordered_map(find, slices(inputs.get("base", ()), GEOMETRY_SLICE), self.ctx.workers, self.ctx):
            for port, table in out:
                emit(port, table)


def _ranks(rows: np.ndarray) -> np.ndarray:
    """Rang (de 1) de chaque ligne dans sa suite de lignes égales consécutives (les voisines d'une même base)."""
    idx = np.arange(len(rows))
    starts = np.r_[True, rows[1:] != rows[:-1]]
    return idx - np.maximum.accumulate(np.where(starts, idx, 0)) + 1
