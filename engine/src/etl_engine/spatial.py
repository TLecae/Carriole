"""Noyau spatial des modificateurs : côté « référence » indexé et préparé, relations par paires, découpes locales.

Règles communes (voir ADR 0029) :
- c'est la grande géométrie qui est préparée (index interne de GEOS), pas chaque petite : tester une parcelle contre un
  département coûte O(log n) au lieu de O(n) ;
- une entité entièrement dedans ou dehors n'est pas recalculée : seules les entités à cheval passent par une découpe ;
- une découpe se fait contre la partie locale d'une grande géométrie (carreaux emboîtés), jamais contre le tout ;
- une paire que GEOS refuse est refaite après réparation, puis rejetée seule : elle n'arrête pas le run.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
import pyarrow as pa
import pyproj
import shapely
from shapely import STRtree
from shapely.errors import GEOSException

from etl_engine.data import geometries, get_crs
from etl_engine.sdk import ConfigError

Predicate = Literal[
    "intersects", "contains", "within", "touches", "crosses", "overlaps", "covers", "covered_by", "dwithin"
]
PREDICATES = ("intersects", "contains", "within", "touches", "crosses", "overlaps", "covers", "covered_by", "dwithin")
# a <prédicat> b  ⇔  b <inverse> a : permet de préparer la géométrie de droite
INVERSE = {
    "intersects": "intersects",
    "contains": "within",
    "within": "contains",
    "touches": "touches",
    "crosses": "crosses",
    "overlaps": "overlaps",
    "covers": "covered_by",
    "covered_by": "covers",
    "dwithin": "dwithin",
}
PREPARE_ABOVE = 512  # sommets : au-delà, une géométrie est préparée avant d'être testée
COPY_VERTICES = 1_000_000  # sommets préparés : en deçà, chaque thread prépare sa propre copie (sinon : verrous)
LOCKS = 64  # verrous des géométries préparées partagées (une géométrie → toujours le même verrou)
SUBDIVIDE_ABOVE = 1024  # sommets : au-delà, une géométrie de référence est découpée en carreaux pour les découpes
PIECE_VERTICES = 256  # sommets visés par carreau
MAX_DEPTH = 16
EMPTY = shapely.Polygon()
_NO_INDEX = np.array([], dtype=np.int64)


@functools.lru_cache(maxsize=256)
def _same_crs(ca: str, cb: str) -> bool:
    """Comparaison mise en cache : elle est refaite à chaque lot par les modificateurs qui lisent en flux."""
    try:
        return pyproj.CRS.from_user_input(ca) == pyproj.CRS.from_user_input(cb)
    except pyproj.exceptions.CRSError:
        return ca == cb


def check_same_crs(a: pa.Table | pa.Schema, b: pa.Table | pa.Schema, what: str = "les deux entrées") -> None:
    """Refuse de comparer deux géométries de systèmes de coordonnées différents (si les deux sont connus)."""
    ca, cb = get_crs(a), get_crs(b)
    if ca and cb and not _same_crs(ca, cb):
        raise ConfigError(f"{what} n'ont pas le même système de coordonnées ({ca} et {cb}) : utiliser un Reproject")


def related_pairs(
    candidates: np.ndarray, others: np.ndarray, predicate: str = "intersects", distance: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """Paires d'indices (i, j) telles que `candidates[i]` <predicate> `others[j]` (géométries nulles ignorées)."""
    if not len(candidates) or not len(others):
        _check_predicate(predicate)
        return _NO_INDEX, _NO_INDEX
    return SpatialIndex.of(others).pairs(candidates, predicate, distance)


def _check_predicate(predicate: str) -> None:
    if predicate not in PREDICATES:
        raise ConfigError(f"relation spatiale inconnue : {predicate}")


class SpatialIndex:
    """Côté « référence » d'un modificateur spatial : décodé et indexé une fois, puis interrogé lot par lot.

    Interrogeable par plusieurs threads à la fois : l'arbre est construit dès la création. Les géométries de plus de
    `PREPARE_ABOVE` sommets sont testées préparées ; or une géométrie préparée de GEOS garde un état interne qui
    change à chaque test (index construits au premier usage, détecteur d'intersections) : deux threads qui la testent
    en même temps la corrompent. Chaque thread prépare donc sa propre copie (quelques grandes géométries, cas
    courant : un département, des communes) ; au-delà de `COPY_VERTICES` sommets en tout, elles sont partagées et
    testées sous verrou (beaucoup de géométries : les threads en testent rarement une même au même moment).
    """

    def __init__(self, table: pa.Table | None = None, geoms: np.ndarray | None = None) -> None:
        self.table = table
        self.geoms = geoms if geoms is not None else geometries(table)
        self.tree = STRtree(self.geoms)
        self.tree.query(shapely.box(0, 0, 0, 0))  # GEOS construit l'arbre à la première requête : pas dans un thread
        self.sizes = shapely.get_num_coordinates(self.geoms)
        big = self.sizes > PREPARE_ABOVE
        self._copies = int(self.sizes[big].sum()) <= COPY_VERTICES
        if not self._copies:
            shapely.prepare(self.geoms[big])
        self._local = threading.local()
        self._locks = [threading.Lock() for _ in range(LOCKS)]
        self._pieces: dict[int, _Pieces] = {}
        self._lock = threading.Lock()

    @classmethod
    def of(cls, geoms: np.ndarray) -> SpatialIndex:
        return cls(geoms=geoms)

    def __len__(self) -> int:
        return len(self.geoms)

    def pairs(
        self, candidates: np.ndarray, predicate: str = "intersects", distance: float = 0.0
    ) -> tuple[np.ndarray, np.ndarray]:
        """Paires (i, j), triées par i puis j : `candidates[i]` <predicate> `geoms[j]` (géométries nulles ignorées)."""
        _check_predicate(predicate)
        if not len(candidates) or not len(self.geoms):
            return _NO_INDEX, _NO_INDEX
        if predicate == "dwithin":
            b = shapely.bounds(candidates)
            ok = ~np.isnan(b[:, 0])
            boxes = np.full(len(candidates), None, dtype=object)
            boxes[ok] = shapely.box(b[ok, 0] - distance, b[ok, 1] - distance, b[ok, 2] + distance, b[ok, 3] + distance)
            i, j = self.tree.query(boxes)
        else:
            i, j = self.tree.query(candidates)  # emprises seulement : le test exact suit, côté préparé
        keep = self.test(candidates, i, j, predicate, distance)
        i, j = i[keep], j[keep]
        order = np.lexsort((j, i))
        return i[order], j[order]

    def test(
        self,
        candidates: np.ndarray,
        i: np.ndarray,
        j: np.ndarray,
        predicate: str,
        distance: float | np.ndarray = 0.0,
    ) -> np.ndarray:
        """`candidates[i]` <predicate> `geoms[j]` pour chaque paire, en préparant la plus grande des deux géométries.

        `distance` (dwithin) : une valeur, ou une par paire.
        """
        _check_predicate(predicate)
        out = np.zeros(len(i), dtype=bool)
        if not len(i):
            return out
        a, b = candidates[i], self.geoms[j]
        sizes = shapely.get_num_coordinates(a)
        by_ref = (self.sizes[j] > PREPARE_ABOVE) & (self.sizes[j] >= sizes)
        dist = np.broadcast_to(np.asarray(distance, dtype=float), len(i))

        def extra(sel: np.ndarray) -> tuple:
            return (dist[sel],) if predicate == "dwithin" else ()

        if by_ref.any():
            # par géométrie de référence : une géométrie préparée est testée contre tous ses candidats, sous verrou
            fn = getattr(shapely, INVERSE[predicate])
            sel = np.flatnonzero(by_ref)
            sel = sel[np.argsort(j[sel], kind="stable")]
            for group in np.split(sel, np.flatnonzero(np.diff(j[sel])) + 1):
                ref = int(j[group[0]])
                if self._copies:
                    out[group] = fn(self._own(ref), a[group], *extra(group))
                    continue
                with self._locks[ref % LOCKS]:
                    out[group] = fn(self.geoms[ref], a[group], *extra(group))
        rest = ~by_ref
        if rest.any():
            # candidats propres au lot (un seul thread) : on peut les préparer sur place
            shapely.prepare(a[rest & (sizes > PREPARE_ABOVE)])
            out[rest] = getattr(shapely, predicate)(a[rest], b[rest], *extra(rest))
        return out

    def local(self, j: np.ndarray, bounds: np.ndarray) -> np.ndarray:
        """Pour chaque paire : la partie de `geoms[j]` qui suffit à découper une entité d'emprise `bounds`.

        Même résultat de découpe (intersection, différence) qu'avec la géométrie entière, mais avec quelques centaines
        de sommets au lieu de dizaines de milliers.
        """
        out = self.geoms[j].copy()
        for k in np.flatnonzero(self.sizes[j] > SUBDIVIDE_ABOVE):
            out[k] = self._pieces_of(int(j[k])).find(bounds[k])
        return out

    def _own(self, j: int) -> shapely.Geometry:
        """Copie préparée de `geoms[j]` propre au thread appelant (faite à son premier usage dans ce thread)."""
        copies = self._local.__dict__.setdefault("copies", {})
        geom = copies.get(j)
        if geom is None:
            geom = copies[j] = shapely.from_wkb(shapely.to_wkb(self.geoms[j]))
            shapely.prepare(geom)
        return geom

    def _pieces_of(self, j: int) -> _Pieces:
        pieces = self._pieces.get(j)
        if pieces is None:
            with self._lock:
                pieces = self._pieces.get(j)
                if pieces is None:
                    pieces = self._pieces[j] = _Pieces(self.geoms[j])
        return pieces


class _Pieces:
    """Une grande géométrie découpée en carreaux emboîtés (arbre quaternaire), jusqu'à ~`PIECE_VERTICES` sommets.

    Pour une emprise, le plus petit carreau qui contient la partie utile de l'emprise donne « géométrie ∩ carreau » :
    l'entité ne peut rencontrer la géométrie que là, la découpe est donc la même.
    """

    def __init__(self, geom: shapely.Geometry) -> None:
        self.rects: list[tuple[float, float, float, float]] = [tuple(geom.bounds)]
        self.geoms: list[shapely.Geometry] = [geom]
        self.kids: list[list[int] | None] = [None]
        dim = shapely.get_dimensions(geom)
        todo = [(0, 0)]
        while todo:
            k, depth = todo.pop()
            if depth >= MAX_DEPTH or shapely.get_num_coordinates(self.geoms[k]) <= PIECE_VERTICES:
                continue
            x0, y0, x1, y1 = self.rects[k]
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            quads = [(x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)]
            try:
                parts = shapely.intersection(self.geoms[k], shapely.box(*np.array(quads).T))
            except GEOSException:
                continue  # géométrie que GEOS ne sait pas découper : ce carreau reste entier (même résultat)
            parts = keep_dimension(parts, np.full(4, dim))
            kids = []
            for quad, part in zip(quads, parts, strict=True):
                kids.append(len(self.geoms))
                self.rects.append(quad)
                self.geoms.append(EMPTY if part is None else part)
                self.kids.append(None)
                todo.append((len(self.geoms) - 1, depth + 1))
            self.kids[k] = kids

    def find(self, bounds: Sequence[float]) -> shapely.Geometry:
        bx0, by0, bx1, by1 = bounds
        k = 0
        while (kids := self.kids[k]) is not None:
            rx0, ry0, rx1, ry1 = self.rects[k]
            # partie utile de l'emprise : dans le carreau (la géométrie n'existe pas au-delà)
            x0, y0, x1, y1 = max(bx0, rx0), max(by0, ry0), min(bx1, rx1), min(by1, ry1)
            xm, ym = (rx0 + rx1) / 2, (ry0 + ry1) / 2
            col = 0 if x1 <= xm else 1 if x0 >= xm else -1
            row = 0 if y1 <= ym else 1 if y0 >= ym else -1
            if col < 0 or row < 0:
                break
            k = kids[row * 2 + col]
        return self.geoms[k]


def keep_dimension(geoms: np.ndarray, dims: np.ndarray) -> np.ndarray:
    """Ne garde de chaque résultat que les parties de la dimension `dims` (celle de l'entité d'origine), None sinon.

    Une découpe de deux polygones qui se touchent seulement donne un trait : ce n'est pas une partie du polygone.
    """
    out = np.asarray(geoms, dtype=object).copy()
    if not len(out):
        return out
    types = shapely.get_type_id(out)
    lower = (types >= 0) & (types != 7) & (shapely.get_dimensions(out) < dims)
    out[lower | shapely.is_empty(out)] = None
    for k in np.flatnonzero(types == 7):  # collection : on ne garde que les parties de bonne dimension
        parts = shapely.get_parts(shapely.get_parts(out[k]))
        parts = parts[shapely.get_dimensions(parts) == dims[k]]
        out[k] = _combine(parts, int(dims[k]))
    return out


def _combine(parts: np.ndarray, dim: int) -> shapely.Geometry | None:
    if not len(parts):
        return None
    if len(parts) == 1:
        return parts[0]
    return (shapely.multipoints, shapely.multilinestrings, shapely.multipolygons)[dim](parts)


def overlay(
    fn: Callable[[np.ndarray, np.ndarray], np.ndarray], a: np.ndarray, b: np.ndarray
) -> tuple[np.ndarray, dict[int, str]]:
    """`fn(a, b)` (intersection, différence…) sur des tableaux de paires, sans qu'une paire fautive arrête le lot.

    Si GEOS refuse le lot, chaque paire est refaite seule, puis après réparation des deux géométries ; celles qui
    échouent encore sont rendues à part : (résultats, {numéro de paire : message}).
    """
    try:
        return fn(a, b), {}
    except GEOSException as exc:
        _raise_if_memory(exc)
    out = np.full(len(a), None, dtype=object)
    errors: dict[int, str] = {}
    for k in range(len(a)):
        try:
            out[k] = fn(a[k], b[k])
        except GEOSException:
            try:
                out[k] = fn(shapely.make_valid(a[k]), shapely.make_valid(b[k]))
            except GEOSException as exc:
                _raise_if_memory(exc)
                errors[k] = str(exc)
    return out, errors


def union_groups(geoms: np.ndarray, starts: np.ndarray) -> tuple[np.ndarray, dict[int, str]]:
    """Union de chaque groupe `geoms[starts[g]:starts[g + 1]]` (un groupe d'une géométrie est rendu tel quel)."""
    out = np.full(len(starts), None, dtype=object)
    if not len(starts):
        return out, {}
    ends = np.r_[starts[1:], len(geoms)]
    single = (ends - starts) == 1
    out[single] = geoms[starts[single]]
    errors: dict[int, str] = {}
    for g in np.flatnonzero(~single):
        try:
            out[g] = shapely.union_all(geoms[starts[g] : ends[g]])
        except GEOSException:
            try:
                out[g] = shapely.union_all(shapely.make_valid(geoms[starts[g] : ends[g]]))
            except GEOSException as exc:
                _raise_if_memory(exc)
                errors[int(g)] = str(exc)
    return out, errors


def _raise_if_memory(exc: GEOSException) -> None:
    """GEOS signale un manque de mémoire par une exception ordinaire : on la rend comme telle."""
    if is_out_of_memory(exc):
        raise MemoryError(str(exc)) from exc


def is_out_of_memory(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, MemoryError) or "bad_alloc" in text or "bad allocation" in text


def zorder(geoms: np.ndarray) -> np.ndarray:
    """Ordre de Morton des centres d'emprise : des géométries proches dans cet ordre sont proches dans le plan."""
    return zorder_bounds(shapely.bounds(geoms))


def zorder_bounds(b: np.ndarray) -> np.ndarray:
    """Comme `zorder`, depuis les emprises (n, 4) : permet de ne pas garder les géométries décodées en mémoire."""
    cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2

    def scale(v: np.ndarray) -> np.ndarray:
        lo, hi = np.nanmin(v), np.nanmax(v)
        return ((v - lo) / ((hi - lo) or 1.0) * 65535).astype(np.uint64)

    def spread(v: np.ndarray) -> np.ndarray:
        v = (v | (v << 8)) & 0x00FF00FF
        v = (v | (v << 4)) & 0x0F0F0F0F
        v = (v | (v << 2)) & 0x33333333
        return (v | (v << 1)) & 0x55555555

    return np.argsort(spread(scale(cx)) | (spread(scale(cy)) << np.uint64(1)), kind="stable")
