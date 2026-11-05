"""OverlayPolygons : découpe des polygones qui se chevauchent en morceaux disjoints."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field
from shapely import STRtree

from etl_engine import duck
from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, set_geometries
from etl_engine.sdk import (
    REJECTED,
    AttributeList,
    ConfigError,
    Emit,
    Modifier,
    Params,
    missing_attributes,
    ordered_map,
    reject,
)
from etl_engine.spatial import SpatialIndex

POLYGONS = ("Polygon", "MultiPolygon")


def _polygonal(geometry):
    """La partie surfacique d'une géométrie réparée (une réparation peut laisser des lignes ou des points)."""
    if geometry.geom_type in POLYGONS:
        return geometry
    parts = [g for g in getattr(geometry, "geoms", []) if g.geom_type in POLYGONS]
    return shapely.union_all(parts) if parts else None


_local = threading.local()


def _spatial() -> duckdb.DuckDBPyConnection | None:
    """Connexion DuckDB du thread, extension spatiale chargée ; None sans l'extension."""
    if not hasattr(_local, "con"):
        _local.con = None
        if duck.spatial_available():
            con = duck.connect("expr")  # un thread, une petite part du budget : le parallélisme vient des paquets
            duck.load_spatial(con)
            _local.con = con
    return _local.con


def _faces(lines: np.ndarray) -> np.ndarray:
    """Faces formées par des contours (`polygonize` de GEOS), après leur union, qui les découpe à chaque croisement.

    `shapely.polygonize` garde le GIL pendant tout son calcul : des dizaines de secondes pour un grand paquet, pendant
    lesquelles les autres paquets et le heartbeat du worker attendent. Celui de DuckDB le libère, avec les mêmes faces
    dans le même ordre. Repli sur shapely sans l'extension spatiale, ou si la connexion manque de mémoire."""
    merged = shapely.union_all(lines)  # libère le GIL
    con = _spatial()
    if con is not None:
        try:
            ((wkb,),) = con.execute(
                "SELECT ST_AsWKB(ST_Polygonize([ST_GeomFromWKB(?)]))", [shapely.to_wkb(merged)]
            ).fetchall()
            return np.asarray(shapely.get_parts(shapely.from_wkb(bytes(wkb))), dtype=object)
        except duckdb.OutOfMemoryException:
            pass
    return np.asarray(shapely.get_parts(shapely.polygonize([merged])), dtype=object)


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
                raise missing_attributes(missing)
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
        workers = max(1, self.ctx.workers)
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
        # seules les géométries invalides sont réparées ; contrôle et réparation sur tous les cœurs
        broken = np.flatnonzero(~_by_slices(shapely.is_valid, valid, workers, self.ctx))
        if len(broken):
            repaired = _by_slices(shapely.make_valid, valid[broken], workers, self.ctx)
            valid[broken] = [_polygonal(g) for g in repaired]
        kept = ~shapely.is_missing(valid)
        positions, valid = positions[kept], valid[kept]
        if not len(valid):
            return
        overlay = _Overlay(valid, p.min_overlap, workers, self.ctx)
        columns_of = _Columns(table, positions, p)
        crs = get_crs(table)
        produced = 0
        edges: list[_Faces] = []
        for inside, edge in ordered_map(overlay.tile, range(len(overlay.tiles)), workers, self.ctx):
            edges.append(edge)
            if len(inside.pieces):
                emit("output", set_geometries(columns_of(inside.offsets, inside.sources), inside.pieces, crs))
                produced += len(inside.pieces)
        for merged in overlay.merge(edges, workers, self.ctx):
            if len(merged.pieces):
                emit("output", set_geometries(columns_of(merged.offsets, merged.sources), merged.pieces, crs))
                produced += len(merged.pieces)
        self.ctx.info(f"{len(positions)} polygones découpés en {produced} morceau(x)")


POLYGON_IDS = (3, 6)  # Polygon, MultiPolygon
TILES_PER_WORKER = 4  # carreaux par worker au moins : les cœurs restent occupés jusqu'au bout du calcul
MIN_TILE_VERTICES = 20_000  # sommets : en deçà, découper encore coûte plus (bords, recollage) que ça ne rapporte
BYTES_PER_VERTEX = 1_000  # mémoire d'un carreau en cours, par sommet de contour (union, faces, copies) : estimée large
LINE_VERTICES = 4_096  # sommets au plus par tronçon de contour : un grand contour n'est découpé que là où il passe
GRID = 1_024  # résolution de la grille de densité des sommets, d'où sont tirés les carreaux


def _by_slices(fn: Any, geoms: np.ndarray, workers: int, ctx: Any) -> np.ndarray:
    """`fn` (fonction vectorisée de shapely, qui libère le GIL) sur des tranches de `geoms`, sur `workers` threads."""
    step = -(-len(geoms) // (workers * TILES_PER_WORKER))
    if workers <= 1 or step < 256:
        return fn(geoms)
    parts = [slice(i, i + step) for i in range(0, len(geoms), step)]
    return np.concatenate(list(ordered_map(lambda s: fn(geoms[s]), parts, workers, ctx)))


def _tile_vertices(total: int, workers: int) -> int:
    """Sommets de contour visés par carreau : assez de carreaux pour occuper tous les cœurs, chacun dans sa part du
    budget mémoire du run (*Paramètres → Calcul*) : `workers` carreaux en cours, autant de résultats en attente."""
    share = duck.run_memory_mb() * 2**20 / duck.heavy_share()
    cap = share / (2 * workers * BYTES_PER_VERTEX)
    return int(max(MIN_TILE_VERTICES, min(cap, total / (TILES_PER_WORKER * workers))))


def _tiles(coords: np.ndarray, extent: tuple[float, float, float, float], target: int) -> np.ndarray:
    """Carreaux (x0, y0, x1, y1) d'un arbre quaternaire sur `extent`, coupés tant qu'ils contiennent plus de `target`
    sommets (comptés sur une grille de `GRID` × `GRID` cases). Deux carreaux voisins ont la même coordonnée de bord,
    calculée par la même formule."""
    x0, y0, x1, y1 = extent
    n = GRID
    ix = np.clip(((coords[:, 0] - x0) / (x1 - x0) * n).astype(np.int64), 0, n - 1)
    iy = np.clip(((coords[:, 1] - y0) / (y1 - y0) * n).astype(np.int64), 0, n - 1)
    grid = np.bincount(iy * n + ix, minlength=n * n).reshape(n, n)
    integral = np.zeros((n + 1, n + 1), dtype=np.int64)
    integral[1:, 1:] = grid.cumsum(0).cumsum(1)

    def count(i0: int, j0: int, i1: int, j1: int) -> int:
        return int(integral[i1, j1] - integral[i0, j1] - integral[i1, j0] + integral[i0, j0])

    cells: list[tuple[int, int, int, int]] = []
    todo = [(0, 0, n, n)]
    while todo:
        i0, j0, i1, j1 = todo.pop()
        if i1 - i0 == 1 or count(i0, j0, i1, j1) <= target:
            cells.append((i0, j0, i1, j1))
            continue
        im, jm = (i0 + i1) // 2, (j0 + j1) // 2
        todo += [(im, jm, i1, j1), (im, j0, i1, jm), (i0, jm, im, j1), (i0, j0, im, jm)]  # sud-ouest d'abord
    c = np.array(cells, dtype=np.float64)
    xs, ys = x0 + c[:, [1, 3]] / n * (x1 - x0), y0 + c[:, [0, 2]] / n * (y1 - y0)
    return np.column_stack([xs[:, 0], ys[:, 0], xs[:, 1], ys[:, 1]])


def _densify(coords: np.ndarray, which: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Contours avec un sommet là où ils traversent une ligne de la grille des carreaux (abscisses `xs`, ordonnées
    `ys`), posé exactement sur la ligne. Calculé une fois pour tous les carreaux : deux carreaux voisins coupent un
    contour au même point, et les morceaux d'une face à cheval se recollent exactement."""
    same = np.flatnonzero(which[1:] == which[:-1])  # segment s : du sommet s au sommet s + 1 d'un même contour
    a, b = coords[same], coords[same + 1]
    points, starts, ts = [coords], [np.arange(len(coords))], [np.zeros(len(coords))]
    for lines, axis in ((xs, 0), (ys, 1)):
        lo = np.searchsorted(lines, np.minimum(a[:, axis], b[:, axis]), side="right")
        hi = np.searchsorted(lines, np.maximum(a[:, axis], b[:, axis]), side="left")
        k = np.maximum(hi - lo, 0)
        total = int(k.sum())
        if not total:
            continue
        seg = np.repeat(np.arange(len(same)), k)
        at = lines[np.repeat(lo, k) + np.arange(total) - np.repeat(np.cumsum(k) - k, k)]
        d = b[seg] - a[seg]
        t = (at - a[seg, axis]) / d[:, axis]
        crossing = a[seg] + t[:, None] * d
        crossing[:, axis] = at  # exactement sur la ligne de la grille
        points.append(crossing)
        starts.append(same[seg])
        ts.append(t)
    if len(points) == 1:
        return coords, which
    start = np.concatenate(starts)
    order = np.lexsort((np.concatenate(ts), start))
    return np.concatenate(points)[order], which[start[order]]


def _cut(coords: np.ndarray, which: np.ndarray, size: int) -> np.ndarray:
    """Contours en tronçons d'au plus `size` + 1 sommets (deux tronçons consécutifs partagent un sommet) : l'index
    spatial ne rend que les tronçons d'un carreau, au lieu d'un grand contour entier."""
    starts = np.flatnonzero(np.r_[True, which[1:] != which[:-1]])
    lengths = np.diff(np.r_[starts, len(which)])
    pos = np.arange(len(which)) - np.repeat(starts, lengths)
    last = np.repeat(lengths, lengths) - 1
    dup = (pos % size == 0) & (pos > 0) & (pos < last)  # fin d'un tronçon et début du suivant
    piece = np.where(pos > 0, (pos - 1) // size, 0)
    per_line = (lengths - 2) // size + 1
    first = np.repeat(np.cumsum(per_line) - per_line, lengths)
    rep = 1 + dup
    ids = np.repeat(first + piece, rep)
    ids[(np.cumsum(rep) - 1)[dup]] += 1  # seconde copie d'un sommet partagé : début du tronçon suivant
    return shapely.linestrings(np.repeat(coords, rep, axis=0), indices=ids)


def _join(pieces: np.ndarray, frames: np.ndarray) -> shapely.Geometry:
    """Réunion des morceaux d'une face à cheval sur plusieurs carreaux (`frames` : le carreau de chaque morceau).

    Ils forment une couverture exacte : mêmes sommets de part et d'autre d'une limite, calculés une fois. La réunion
    de couverture est alors linéaire (3 s au lieu de 16 pour un réseau d'un million de sommets) ; mais GEOS ne
    vérifie pas son entrée. Contrôle : chaque sommet posé sur le bord du carreau de son morceau doit appartenir à
    deux morceaux au moins. Sinon (sommet déplacé par un calcul d'intersection difficile, contour d'un polygone posé
    sur une limite), réunion complète."""
    coords, which = shapely.get_coordinates(pieces, return_index=True)
    x, y, f = coords[:, 0], coords[:, 1], frames[which]
    on = (x == f[:, 0]) | (x == f[:, 2]) | (y == f[:, 1]) | (y == f[:, 3])  # dans le carreau : sur son bord
    seams = np.unique(np.column_stack([which[on], coords[on]]), axis=0)  # un sommet par morceau (anneaux fermés)
    _, shared = np.unique(seams[:, 1:], axis=0, return_counts=True)
    if (shared >= 2).all():
        return shapely.coverage_union_all(pieces)
    return shapely.union_all(pieces)


class _Faces:
    """Faces et polygones qui les recouvrent : ceux de la face k sont `sources[offsets[k]:offsets[k + 1]]` (triés)."""

    def __init__(self, pieces: np.ndarray, offsets: np.ndarray, sources: np.ndarray, tile: int = -1) -> None:
        self.pieces, self.offsets, self.sources = pieces, offsets, sources
        self.tile = tile  # carreau des morceaux à recoller

    @classmethod
    def empty(cls) -> _Faces:
        return cls(np.array([], dtype=object), np.zeros(1, dtype=np.int64), np.array([], dtype=np.int64))


class _Overlay:
    """Découpage par carreaux, sur tous les cœurs, exact et sans doublon.

    Le plan est découpé en carreaux qui contiennent chacun à peu près le même nombre de sommets de contours (leur taille
    vient du budget mémoire et du nombre de cœurs). Chaque carreau calcule les faces formées par les contours coupés à
    ses bords, avec son propre contour. Une face qui ne touche pas un bord du carreau est une face de l'arrangement
    entier : elle sort telle quelle. Les faces qui touchent un bord sont des morceaux de faces à cheval sur plusieurs
    carreaux : à la fin, les morceaux recouverts par les mêmes polygones sont réunis (deux faces voisines d'un
    arrangement n'ont jamais les mêmes polygones recouvrants : seule une limite de carreau peut les séparer).
    Un très grand polygone (un réseau d'un seul tenant) est ainsi réparti entre tous les carreaux et tous les cœurs.
    """

    def __init__(self, valid: np.ndarray, min_overlap: int, workers: int, ctx: Any) -> None:
        self.min_overlap = max(1, min_overlap)
        self.index = SpatialIndex.of(valid)  # polygones préparés : ceux qui recouvrent chaque face
        rings = shapely.get_parts(_by_slices(shapely.boundary, valid, workers, ctx))
        coords, which = shapely.get_coordinates(rings, return_index=True)
        del rings
        x0, y0, x1, y1 = shapely.total_bounds(valid)
        pad = max(x1 - x0, y1 - y0) * 0.01 + 1.0  # bords extérieurs hors de tout polygone
        extent = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        self.tiles = _tiles(coords, extent, _tile_vertices(len(coords), workers))
        self.xs, self.ys = np.unique(self.tiles[:, [0, 2]]), np.unique(self.tiles[:, [1, 3]])
        coords, which = _densify(coords, which, self.xs, self.ys)
        self.lines = _cut(coords, which, LINE_VERTICES)
        self.tree = STRtree(self.lines)
        self.tree.query(shapely.box(0, 0, 0, 0))  # arbre construit ici, pas dans un thread
        self.corners = np.unique(
            np.concatenate(
                [self.tiles[:, [0, 1]], self.tiles[:, [2, 3]], self.tiles[:, [0, 3]], self.tiles[:, [2, 1]]]
            ),
            axis=0,
        )

    def _frame(self, x0: float, y0: float, x1: float, y1: float) -> shapely.Geometry:
        """Contour du carreau, avec un sommet à chaque coin d'un carreau voisin posé sur ses bords : les morceaux d'une
        face à cheval ont alors les mêmes sommets des deux côtés d'une limite (une couverture exacte, réunie vite)."""
        c = self.corners
        x, y = c[:, 0], c[:, 1]
        bottom = np.sort(x[(y == y0) & (x > x0) & (x < x1)])
        right = np.sort(y[(x == x1) & (y > y0) & (y < y1)])
        top = np.sort(x[(y == y1) & (x > x0) & (x < x1)])[::-1]
        left = np.sort(y[(x == x0) & (y > y0) & (y < y1)])[::-1]
        ring = [
            [(x0, y0)],
            np.column_stack([bottom, np.full(len(bottom), y0)]),
            [(x1, y0)],
            np.column_stack([np.full(len(right), x1), right]),
            [(x1, y1)],
            np.column_stack([top, np.full(len(top), y1)]),
            [(x0, y1)],
            np.column_stack([np.full(len(left), x0), left]),
            [(x0, y0)],
        ]
        return shapely.linestrings(np.concatenate([np.asarray(p, dtype=np.float64).reshape(-1, 2) for p in ring]))

    def tile(self, k: int) -> tuple[_Faces, _Faces]:
        """(faces entières, morceaux de faces qui touchent un bord) du carreau `k`, recouvertes assez de fois."""
        x0, y0, x1, y1 = self.tiles[k]
        lines = shapely.clip_by_rect(self.lines[self.tree.query(shapely.box(x0, y0, x1, y1))], x0, y0, x1, y1)
        lines = lines[~shapely.is_empty(lines)]
        if not len(lines):
            return _Faces.empty(), _Faces.empty()
        frame = self._frame(x0, y0, x1, y1)
        pieces = _faces(np.append(lines, np.array([frame], dtype=object)))
        if not len(pieces):
            return _Faces.empty(), _Faces.empty()
        face, source = self.index.pairs(shapely.point_on_surface(pieces), "within")  # triées par face
        counts = np.bincount(face, minlength=len(pieces))
        kept = counts >= self.min_overlap
        b = shapely.bounds(pieces)
        edge = (b[:, 0] <= x0) | (b[:, 1] <= y0) | (b[:, 2] >= x1) | (b[:, 3] >= y1)

        def faces(mask: np.ndarray) -> _Faces:
            sel = np.flatnonzero(mask)
            offsets = np.concatenate(([0], np.cumsum(counts[sel])))
            return _Faces(pieces[sel], offsets, source[mask[face]], k)

        return faces(kept & ~edge), faces(kept & edge)

    def merge(self, edges: list[_Faces], workers: int, ctx: Any) -> Any:
        """Faces à cheval sur plusieurs carreaux : leurs morceaux réunis, par groupes de mêmes polygones recouvrants.
        Les parties d'une réunion qui ne se touchent que par un point restent des faces distinctes."""
        pieces = np.concatenate([e.pieces for e in edges]) if edges else np.array([], dtype=object)
        if not len(pieces):
            return
        sources = np.concatenate([e.sources for e in edges])
        counts = np.concatenate([np.diff(e.offsets) for e in edges])
        frames = self.tiles[np.concatenate([np.full(len(e.pieces), e.tile) for e in edges])]
        bounds = np.concatenate(([0], np.cumsum(counts)))
        groups: dict[bytes, list[int]] = {}
        for k in range(len(pieces)):  # morceaux du bord des carreaux seulement : une petite partie des faces
            groups.setdefault(sources[bounds[k] : bounds[k + 1]].tobytes(), []).append(k)
        members = list(groups.values())

        def join(batch: list[list[int]]) -> _Faces:
            out, owners = [], []
            for group in batch:
                parts = (
                    [pieces[group[0]]] if len(group) == 1 else shapely.get_parts(_join(pieces[group], frames[group]))
                )
                out.extend(parts)
                owners.extend([group[0]] * len(parts))
            owner = np.array(owners, dtype=np.int64)
            offsets = np.concatenate(([0], np.cumsum(counts[owner])))
            taken = np.concatenate([sources[bounds[o] : bounds[o + 1]] for o in owner]) if len(owner) else sources[:0]
            return _Faces(np.array(out, dtype=object), offsets, taken)

        step = max(1, -(-len(members) // (workers * TILES_PER_WORKER)))
        batches = [members[i : i + step] for i in range(0, len(members), step)]
        yield from ordered_map(join, batches, workers, ctx)


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
