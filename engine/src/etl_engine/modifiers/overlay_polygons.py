"""OverlayPolygons : découpe des polygones qui se chevauchent en morceaux disjoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, set_geometries
from etl_engine.sdk import REJECTED, AttributeList, ConfigError, Emit, Modifier, Params, ordered_map, reject
from etl_engine.spatial import SUBDIVIDE_ABOVE, SpatialIndex, zorder

POLYGONS = ("Polygon", "MultiPolygon")


def _polygonal(geometry):
    """La partie surfacique d'une géométrie réparée (une réparation peut laisser des lignes ou des points)."""
    if geometry.geom_type in POLYGONS:
        return geometry
    parts = [g for g in getattr(geometry, "geoms", []) if g.geom_type in POLYGONS]
    return shapely.union_all(parts) if parts else None


class OverlayPolygons(Modifier):
    name = "OverlayPolygons"
    label = "Superposition de polygones"
    category = "Géométrie"
    description = "Découpe des polygones qui se chevauchent en morceaux disjoints, avec le nombre de polygones qui les recouvrent."
    help = (
        "Tous les polygones reçus sont superposés : leurs contours découpent le plan en morceaux, et chaque morceau sort "
        "sur « output » avec `_overlap_count` (le nombre de polygones d'origine qui le recouvrent), `_origins` (leurs numéros, "
        "de 0, dans l'ordre d'arrivée) et, pour chaque attribut de « Attributs à lister », la liste des valeurs des "
        "polygones qui le recouvrent. Les morceaux qu'aucun polygone ne recouvre (trous, vide autour) ne sont pas produits. "
        "« Recouvrement minimal » ne garde que les morceaux recouverts au moins N fois (2 = les zones de conflit). "
        "Les entités qui ne sont pas des polygones partent sur « Rejected ». Toutes les entités sont lues avant le calcul."
    )
    mode = "blocking"
    outputs = ("output",)

    class Params(Params):
        attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs à lister",
            description="Pour chaque morceau : la liste des valeurs de ces attributs sur les polygones qui le recouvrent.",
        )
        min_overlap: int = Field(
            1,
            ge=1,
            title="Recouvrement minimal",
            description="Ne garde que les morceaux recouverts au moins ce nombre de fois.",
        )
        count_attribute: str = Field(
            "_overlap_count", title="Attribut du nombre", description="Nombre de polygones qui recouvrent le morceau."
        )
        origins_attribute: str = Field(
            "_origins",
            title="Attribut des origines",
            description="Numéros (de 0) des polygones qui recouvrent le morceau ; vide = aucun.",
        )

    def _names(self, schema: pa.Schema | None = None) -> list[str]:
        p = self.params
        if schema is not None:
            missing = [a for a in p.attributes if a not in schema.names]
            if missing:
                raise ConfigError(f"attributs absents : {', '.join(missing)}")
        return [p.count_attribute, *([p.origins_attribute] if p.origins_attribute else []), *p.attributes]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        self._names(schema)
        p = self.params
        fields = [pa.field(p.count_attribute, pa.int64())]
        if p.origins_attribute:
            fields.append(pa.field(p.origins_attribute, pa.list_(pa.int64())))
        fields += [pa.field(a, pa.list_(schema.field(a).type)) for a in p.attributes]
        return {"output": pa.schema([*fields, schema.field(GEOM)], metadata=schema.metadata)}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        if GEOM not in table.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        self._names(table.schema)
        geoms = geometries(table)
        types = shapely.get_type_id(geoms)
        ok = ~shapely.is_missing(geoms) & np.isin(types, POLYGON_IDS) & ~shapely.is_empty(geoms)
        if not ok.all():
            emit(REJECTED, reject(table.filter(pa.array(~ok)), "pas de polygone"))
        positions = np.flatnonzero(ok)
        if not len(positions):
            return
        valid = geoms[ok]
        del geoms
        broken = np.flatnonzero(~shapely.is_valid(valid))  # seules les géométries invalides sont réparées
        if len(broken):
            valid[broken] = [_polygonal(g) for g in shapely.make_valid(valid[broken])]
        kept = ~shapely.is_missing(valid)
        positions, valid = positions[kept], valid[kept]
        if not len(valid):
            return
        overlay = _Overlay(valid, p.min_overlap)
        columns_of = _Columns(table, positions, p)
        chunks = [overlay.order[i : i + CHUNK] for i in range(0, len(valid), CHUNK)]
        produced = 0
        for pieces, offsets, sources in ordered_map(overlay.chunk, chunks, self.ctx.workers, self.ctx):
            if len(pieces):
                emit("output", set_geometries(columns_of(offsets, sources), pieces, get_crs(table)))
                produced += len(pieces)
        self.ctx.info(f"{len(positions)} polygones découpés en {produced} morceau(x)")


POLYGON_IDS = (3, 6)  # Polygon, MultiPolygon
CHUNK = 5_000  # polygones « possédés » par un paquet (voir _Overlay)


class _Overlay:
    """Découpage par paquets de polygones voisins, exact et sans doublon.

    Un morceau est contenu dans chacun des polygones qui le recouvrent, en particulier dans celui de plus petit numéro :
    seuls les polygones qui touchent l'emprise de celui-ci peuvent le découper. Un paquet calcule donc l'arrangement de
    ses polygones et de ceux qui touchent leurs emprises, et ne garde que les morceaux dont le plus petit polygone
    recouvrant est à lui. Les paquets sont indépendants : traités en parallèle, mémoire bornée par paquet.
    Ces morceaux sont dans l'emprise du paquet : les contours des grands polygones voisins y sont d'abord coupés.
    """

    def __init__(self, valid: np.ndarray, min_overlap: int) -> None:
        self.valid = valid
        self.min_overlap = min_overlap
        self.index = SpatialIndex.of(valid)  # grands polygones préparés pour situer les morceaux
        self.tree = self.index.tree
        self.order = zorder(valid)  # paquets de polygones proches

    def chunk(self, members: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(morceaux, bornes, polygones recouvrants) : ceux du morceau k sont `sources[offsets[k]:offsets[k + 1]]`."""
        near = np.unique(self.tree.query(shapely.box(*shapely.bounds(self.valid[members]).T))[1])
        lines = shapely.boundary(self.valid[near])
        big = np.flatnonzero(self.index.sizes[near] > SUBDIVIDE_ABOVE)
        if len(big):
            x0, y0, x1, y1 = shapely.total_bounds(self.valid[members])
            pad = max(x1 - x0, y1 - y0) * 0.01 + 1.0
            lines[big] = shapely.intersection(lines[big], shapely.box(x0 - pad, y0 - pad, x1 + pad, y1 + pad))
        pieces = np.asarray(shapely.get_parts(shapely.polygonize([shapely.union_all(lines)])), dtype=object)
        if not len(pieces):
            return pieces, np.zeros(1, dtype=np.int64), np.array([], dtype=np.int64)
        piece_idx, source = self.index.pairs(shapely.point_on_surface(pieces), "within")  # triées par morceau
        counts = np.bincount(piece_idx, minlength=len(pieces))
        bounds = np.concatenate(([0], np.cumsum(counts)))
        smallest = np.full(len(pieces), -1, dtype=np.int64)
        covered = counts > 0
        smallest[covered] = source[bounds[:-1][covered]]  # sources triées : la première est la plus petite
        mine = np.zeros(len(self.valid), dtype=bool)
        mine[members] = True
        keep = np.flatnonzero(covered & (counts >= self.min_overlap) & mine[np.maximum(smallest, 0)])
        offsets = np.concatenate(([0], np.cumsum(counts[keep])))
        sources = np.concatenate([source[bounds[k] : bounds[k + 1]] for k in keep]) if len(keep) else source[:0]
        return pieces[keep], offsets, sources


class _Columns:
    """Colonnes de sortie d'un paquet de morceaux : nombre, origines, listes d'attributs."""

    def __init__(self, table: pa.Table, positions: np.ndarray, p: Any) -> None:
        self.table, self.positions, self.p = table, positions, p

    def __call__(self, offsets: np.ndarray, sources: np.ndarray) -> pa.Table:
        p = self.p
        rows = pa.array(self.positions[sources], pa.int64())  # numéros de ligne d'origine (de 0, ordre d'arrivée)
        offsets_arr = pa.array(offsets.astype(np.int32), pa.int32())
        columns: dict[str, pa.Array] = {p.count_attribute: pa.array(np.diff(offsets), pa.int64())}
        if p.origins_attribute:
            columns[p.origins_attribute] = pa.ListArray.from_arrays(offsets_arr, rows)
        for a in p.attributes:
            values = self.table.column(a).take(rows).combine_chunks()
            columns[a] = pa.ListArray.from_arrays(offsets_arr, values)
        return pa.table({**columns, GEOM: pa.nulls(len(offsets) - 1, pa.binary())})
