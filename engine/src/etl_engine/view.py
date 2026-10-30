"""Vue cartographique d'un port, prête pour les tuiles (ADR 0010, ADR 0030, ADR 0034, ADR 0037).

Construite à la demande, en arrière-plan, quand l'inspecteur ouvre un port (jamais pendant le run), à côté de
`<port>.parquet` : `<port>.view.parquet`. Une ligne par **morceau** de géométrie et par **niveau** :

- `lvl = FULL` : chaque entité éclatée en ses morceaux (un polygone d'un multipolygone, une ligne), reprojetés en
  EPSG:3857 ; `lvl = POINTS` : les points, servis à tous les zooms ;
- `lvl = L` (`LEVELS`) : généralisation pour les zooms L et L+1 : morceaux d'au moins un pixel (au zoom L) simplifiés
  au demi-pixel du zoom L+1 ; plus petits, un **point** par entité et par cellule (un pixel du zoom L+1) : sans
  géométrie, la cellule est dans `bbox` (la tuile en fait un carré d'un pixel) ;
- `bbox` : emprise du morceau (EPSG:3857) pour filtrer une tuile sans décoder les géométries ; `fid` : la ligne
  d'origine, identifiant commun à la table et à la carte ; `esize` : taille de l'entité entière (plus grand côté de
  son emprise), pour ne dessiner que les plus grandes d'une vue trop chargée ;
- `role` : `PART` (un morceau tel quel), `FILL` / `EDGE` (surface et contour d'un morceau découpé, voir plus bas).

Une tuile ne lit que les morceaux de son niveau dans son emprise : une entité énorme (parcelles fusionnées sur une
région : 10 millions de sommets, 300 000 morceaux) ne coûte pas plus qu'un jeu de petites entités. Lignes triées par
niveau puis selon la courbe de Hilbert (morceaux proches dans les mêmes row groups).

Un morceau de plus de `PIECE_VERTICES` sommets (polygone fusionné d'un seul tenant, avec des milliers de trous) est
traité à part, en Python (ADR 0046) : simplifié anneau par anneau (`simplify_polygons` : GEOS valide et répare un
polygone simplifié, de 30 s à plusieurs minutes pour un seul de ces polygones), puis **découpé** sur la grille des
tuiles (`subdivide`) : une tuile ne lit que les pièces de son emprise. Les pièces d'un polygone découpé sont des
surfaces sans contour (`FILL`) ; son contour (anneaux en lignes, découpés aussi) est à part (`EDGE`) : les coupes ne
se voient pas.

À côté, `<port>.view.ent.parquet` : une ligne par entité, triée selon la courbe de Hilbert (type de la géométrie
entière, nombre de sommets, emprise, taille) : la table, le comptage par zone (choix des entités d'une tuile) et la
sélection sur la carte s'en servent sans lire les géométries ; le tri spatial évite de tout lire pour une petite
zone.

Le fichier du port n'est jamais réordonné ni modifié. Un fichier `<port>.view.json` décrit la vue (version, SCR,
nombre d'entités, types de géométrie, emprise).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from etl_engine import duck
from etl_engine.cache import port_data
from etl_engine.data import GEOM, get_crs

VERSION = 5  # une vue d'une autre version est reconstruite
ROW_GROUP_SIZE = 50_000
ROW_GROUP_BYTES = "8MB"  # au plus par row group de la vue : une tuile n'en décompresse pas plus (pièces énormes)
WEB_MERCATOR = "EPSG:3857"
WORLD = 20037508.342789244  # demi-largeur du monde en EPSG:3857
TILE_PIXELS = 512
LEVELS = (0, 2, 4, 6, 8, 10)  # niveaux généralisés : le niveau L sert aux zooms L et L+1
FULL = 99  # morceaux entiers, à partir du zoom FULL_FROM
POINTS = -1  # points, à tous les zooms
FULL_FROM = 12
FULL_MAX_ZOOM = 15  # zoom maximal des tuiles demandées par la carte (au-delà, elle agrandit celles-ci)
PIECE_VERTICES = 4096  # au-delà, un morceau est simplifié sans validation et découpé
SMALL_HOLE = 256  # sommets d'un trou remis dans les pièces après la découpe (les plus grands sont coupés avec elles)
PART, FILL, EDGE = 0, 1, 2  # rôle d'une ligne de la vue : morceau tel quel, surface découpée, contour découpé
WORLD_BOX = f"{{'min_x': {-WORLD}, 'min_y': {-WORLD}, 'max_x': {WORLD}, 'max_y': {WORLD}}}::BOX_2D"
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()
log = logging.getLogger("etl.view")


def pixel(z: int) -> float:
    """Taille d'un pixel au zoom `z`, en mètres EPSG:3857 (tuiles de 512 pixels)."""
    return 2 * WORLD / (TILE_PIXELS * 2**z)


def level_for(z: int) -> int:
    """Niveau de la vue lu pour une tuile de zoom `z`."""
    return FULL if z >= FULL_FROM else max(lv for lv in LEVELS if lv <= z)


def tile_size(z: int) -> float:
    """Côté d'une tuile de zoom `z`, en mètres EPSG:3857."""
    return 2 * WORLD / 2**z


def simplify_polygons(g: np.ndarray, tolerance: float) -> np.ndarray:
    """Polygones ou multipolygones simplifiés anneau par anneau, comme des lignes (Douglas-Peucker) ; None pour une
    géométrie effondrée.

    `shapely.simplify` (comme `ST_Simplify`) valide puis répare chaque polygone simplifié : sur un polygone fusionné de
    750 000 sommets et 10 000 trous, 30 s (plus de 5 minutes à 1,5 million de sommets), contre 0,1 s ici. Les trous
    effondrés ou plus petits qu'un pixel (deux fois `tolerance`, un demi-pixel) disparaissent, et les polygones dont le
    contour s'effondre. Le résultat peut être invalide : il suffit pour dessiner. Un peu plus lent que
    `shapely.simplify` sur de petits polygones : réservé aux gros."""
    out = np.full(len(g), None, dtype=object)
    parts, which = shapely.get_parts(g, return_index=True)
    rings, owner = shapely.get_rings(parts, return_index=True)
    if not len(rings):
        return out
    shell = np.r_[True, owner[1:] != owner[:-1]]  # le premier anneau d'un polygone est son contour
    b = shapely.bounds(rings)
    seen = shell | (np.fmax(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) >= 2 * tolerance)
    simple = np.full(len(rings), None, dtype=object)
    simple[seen] = shapely.simplify(rings[seen], tolerance, preserve_topology=False)
    ok = seen & (shapely.get_num_coordinates(simple) >= 4)  # un anneau effondré devient une ligne de 2 ou 3 points
    alive = np.zeros(len(parts), bool)
    alive[owner[shell]] = ok[shell]
    keep = ok & alive[owner]
    polygons = np.full(len(parts), None, dtype=object)
    if keep.any():
        shapely.polygons(simple[keep], indices=owner[keep], out=polygons)
    found = ~shapely.is_missing(polygons)
    single = (shapely.get_type_id(g) == 3)[which]
    out[which[single & found]] = polygons[single & found]
    if (multi := ~single & found).any():
        shapely.multipolygons(polygons[multi], indices=which[multi], out=out)
    return out


def _halves(box: tuple[float, ...], depth: int) -> tuple[tuple[float, ...], tuple[float, ...], float, int]:
    """Les deux moitiés de `box` (en long aux profondeurs paires, en large sinon), la coupe et son axe (0 : x)."""
    x0, y0, x1, y1 = box
    if depth % 2 == 0:
        m = (x0 + x1) / 2
        return (x0, y0, m, y1), (m, y0, x1, y1), m, 0
    m = (y0 + y1) / 2
    return (x0, y0, x1, m), (x0, m, x1, y1), m, 1


def subdivide(g: np.ndarray, finest: float, pool: ThreadPoolExecutor | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Géométries de plus de `PIECE_VERTICES` sommets coupées en pièces : (pièces, indice de leur géométrie).

    Coupes en deux successives, en long puis en large, sur la grille des tuiles (une tuile ne lit que les pièces de son
    emprise), jusqu'à `PIECE_VERTICES` sommets par pièce, ou jusqu'à une pièce de la taille `finest` (tuile du zoom le
    plus fin servi : découper plus fin n'allège plus aucune tuile). `clip_by_rect` coupe exactement (deux pièces
    voisines se touchent, sans jour ni chevauchement) et ne répare rien ; les coupes d'un même tour se font en
    parallèle (`pool` : GEOS libère le GIL).

    Un polygone est coupé sans ses petits trous, remis ensuite dans chaque pièce (`_holes_back`) : GEOS cherche la
    pièce de chaque trou sans index (7,7 s pour couper en deux un polygone de 1,6 million de sommets et 21 000 trous,
    0,04 s sans ses trous). Les grands trous (plus de `SMALL_HOLE` sommets, sur des kilomètres parfois) restent dans
    le polygone : peu nombreux, ils sont coupés avec lui."""
    rings, of = shapely.get_rings(g, return_index=True)
    polygon = shapely.get_type_id(g) == 3
    hole = np.r_[False, of[1:] == of[:-1]] & polygon[of]  # anneaux d'un polygone après le premier : ses trous
    small = hole & (shapely.get_num_coordinates(rings) <= SMALL_HOLE)
    holes = Holes(shapely.polygons(rings[small]), of[small])
    shells = g.copy()
    if polygon.any():  # chaque polygone sans ses petits trous (les autres géométries restent telles quelles)
        shapely.polygons(rings[~small], indices=of[~small], out=shells)
    owners: list[int] = []
    leaves: list[tuple[Any, tuple[float, ...], np.ndarray]] = []
    todo = [
        (geom, i, (-WORLD, -WORLD, WORLD, WORLD), 0, np.flatnonzero(holes.owner == i)) for i, geom in enumerate(shells)
    ]
    while todo:
        halves = []
        for geom, i, box, depth, inside in todo:
            bounds = shapely.bounds(geom)
            while True:
                size = shapely.get_num_coordinates(geom) + holes.vertices[inside].sum()
                if size <= PIECE_VERTICES or max(box[2] - box[0], box[3] - box[1]) <= finest:
                    leaves.append((geom, box, inside))
                    owners.append(i)
                    break
                a, b, cut, axis = _halves(box, depth)
                depth += 1
                if bounds[axis + 2] <= cut:  # toute la géométrie d'un côté : on y descend sans couper
                    box = a
                elif bounds[axis] >= cut:
                    box = b
                else:
                    halves += [(geom, i, half, depth, holes.touching(inside, half)) for half in (a, b)]
                    break
        cut_pieces = (pool.map if pool else map)(lambda h: shapely.clip_by_rect(h[0], *h[2]), halves)
        todo = [(p, *rest) for p, (_, *rest) in zip(cut_pieces, halves, strict=True) if not shapely.is_empty(p)]
    pieces = list((pool.map if pool else map)(lambda leaf: _holes_back(*leaf, holes), leaves))
    found = [(p, i) for ps, i in zip(pieces, owners, strict=True) for p in ps]
    return np.array([p for p, _ in found], dtype=object), np.array([i for _, i in found], dtype=np.int64)


class Holes:
    """Trous des polygones à découper : leurs polygones, leur polygone d'origine, emprises, sommets, un point de chaque."""

    def __init__(self, polygons: np.ndarray, owner: np.ndarray) -> None:
        self.polygons, self.owner = polygons, owner
        self.bounds = shapely.bounds(polygons).reshape(-1, 4)
        self.vertices = shapely.get_num_coordinates(polygons)
        self.points = shapely.point_on_surface(polygons)

    def touching(self, inside: np.ndarray, box: tuple[float, ...]) -> np.ndarray:
        b = self.bounds[inside]
        return inside[(b[:, 0] <= box[2]) & (b[:, 2] >= box[0]) & (b[:, 1] <= box[3]) & (b[:, 3] >= box[1])]


def _holes_back(shell: Any, box: tuple[float, ...], inside: np.ndarray, holes: Holes) -> list[Any]:
    """Pièce `shell` (contour coupé à `box`) avec ses trous : ceux tout entiers dans `box` deviennent des anneaux
    intérieurs de la pièce qui les contient (index des points des trous, pièces préparées) ; ceux coupés par `box`
    sont retirés de la pièce (peu nombreux, petits)."""
    if not len(inside):
        return [shell]
    parts = shapely.get_parts(shell)
    parts = parts[shapely.get_type_id(parts) == 3]
    b = holes.bounds[inside]
    whole = (b[:, 0] >= box[0]) & (b[:, 2] <= box[2]) & (b[:, 1] >= box[1]) & (b[:, 3] <= box[3])
    inner = inside[whole]
    if len(inner):
        part, hole = shapely.STRtree(holes.points[inner]).query(parts, predicate="contains")
        rings = np.concatenate(
            [shapely.get_exterior_ring(parts), shapely.get_exterior_ring(holes.polygons[inner[hole]])]
        )
        index = np.concatenate([np.arange(len(parts)), part])
        order = np.argsort(index, kind="stable")  # pour chaque pièce : son contour, puis ses trous
        parts = shapely.polygons(rings[order], indices=index[order])
    if len(crossing := inside[~whole]):
        # trous petits : réparés sans frais ; une pièce invalide (simplifiée sans réparation) garde ses trous coupés
        with contextlib.suppress(shapely.errors.GEOSException):
            cut = shapely.union_all(shapely.make_valid(shapely.clip_by_rect(holes.polygons[crossing], *box)))
            shapely.prepare(cut)
            hit = shapely.intersects(cut, parts)  # le calcul de surface, pour les seules pièces touchées
            parts = np.concatenate([parts[~hit], shapely.get_parts(shapely.difference(parts[hit], cut))])
        parts = parts[(shapely.get_type_id(parts) == 3) & ~shapely.is_empty(parts)]
    return [shapely.multipolygons(parts)] if len(parts) > 1 else list(parts)  # une pièce par case


def _huge_rows(
    fid: np.ndarray, esize: np.ndarray, psize: np.ndarray, g: np.ndarray, pool: ThreadPoolExecutor, workers: int
) -> pa.Table | None:
    """Lignes de la vue des morceaux énormes `g` (EPSG:3857) : `FULL`, puis chaque niveau, simplifiés et découpés (les
    morceaux de moins d'un pixel d'un niveau sont ses cellules, calculées en SQL comme les autres)."""
    polygonal = shapely.get_dimensions(g) == 2
    tables = []
    for lv in (FULL, *LEVELS):
        if lv == FULL:
            simple, finest = g, tile_size(FULL_MAX_ZOOM)
        else:
            keep, tolerance, finest = psize >= pixel(lv), pixel(lv + 2), tile_size(lv + 1)
            simple = np.full(len(g), None, dtype=object)
            chunks = [c for c in np.array_split(np.flatnonzero(keep & polygonal), workers) if len(c)]
            done = pool.map(partial(simplify_polygons, tolerance=tolerance), [g[c] for c in chunks])
            for c, found in zip(chunks, done, strict=True):
                simple[c] = found
            lines = keep & ~polygonal
            simple[lines] = shapely.simplify(g[lines], tolerance, preserve_topology=False)
        rows = np.flatnonzero(~(shapely.is_missing(simple) | shapely.is_empty(simple)))
        if not len(rows):
            continue
        pieces, owner = subdivide(simple[rows], finest, pool)
        owner = rows[owner]
        cut = (np.bincount(owner, minlength=len(g)) > 1) & polygonal
        role = np.where(cut[owner], FILL, PART).astype(np.uint8)
        if len(edged := np.flatnonzero(cut)):  # contour d'un polygone découpé : ses anneaux en lignes, découpés
            edges, of = subdivide(shapely.boundary(simple[edged]), finest, pool)
            pieces, owner = np.concatenate([pieces, edges]), np.concatenate([owner, edged[of]])
            role = np.concatenate([role, np.full(len(edges), EDGE, np.uint8)])
        b = shapely.bounds(pieces)
        bbox = pa.StructArray.from_arrays([pa.array(b[:, k]) for k in range(4)], ["xmin", "ymin", "xmax", "ymax"])
        tables.append(
            pa.table(
                {
                    "fid": pa.array(fid[owner], pa.int64()),
                    "lvl": pa.array(np.full(len(pieces), lv), pa.int32()),
                    "esize": pa.array(esize[owner], pa.float64()),
                    "role": pa.array(role, pa.uint8()),
                    "bbox": bbox,
                    "geom_3857": pa.array(shapely.to_wkb(pieces), pa.binary()),
                }
            )
        )
    return pa.concat_tables(tables) if tables else None


def _stem(port_file: Path) -> str:
    return port_file.name.removesuffix(".parquet")


def view_path(port_file: Path) -> Path:
    return port_file.with_name(f"{_stem(port_file)}.view.parquet")


def entities_path(port_file: Path) -> Path:
    return port_file.with_name(f"{_stem(port_file)}.view.ent.parquet")


def view_meta_path(port_file: Path) -> Path:
    return port_file.with_name(f"{_stem(port_file)}.view.json")


def _lit(value: str) -> str:
    """Littéral SQL texte."""
    return "'" + value.replace("'", "''") + "'"


def read_view_meta(port_file: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads(view_meta_path(port_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ready = view_path(port_file).is_file() and entities_path(port_file).is_file()
    return meta if meta.get("version") == VERSION and ready else None


def ensure_view(port_file: Path) -> dict[str, Any] | None:
    """La vue de `port_file`, construite si besoin ; ses métadonnées.

    None : pas de géométrie ou SCR inconnu. Deux demandes simultanées de la même vue n'en construisent qu'une.
    """
    meta = read_view_meta(port_file)
    if meta is not None:
        return meta
    with _locks_guard:
        lock = _locks.setdefault(view_path(port_file), threading.Lock())
    with lock:
        meta = read_view_meta(port_file)
        if meta is None:
            _discard_others(port_file)
            meta = build_view(port_file)
    return meta


def _bbox(x0: str, y0: str, x1: str, y1: str) -> str:
    return f"{{'xmin': {x0}, 'ymin': {y0}, 'xmax': {x1}, 'ymax': {y1}}}"


def _hilbert(x: str, y: str) -> str:
    """Rang sur la courbe de Hilbert du monde (EPSG:3857) : des lignes voisines dans le fichier sont proches."""
    return f"ST_Hilbert({x}, {y}, {WORLD_BOX})"


HUGE_COPIES = 32  # mémoire d'un morceau énorme en cours de traitement : environ 32 fois son WKB (niveaux, pièces)


def _write_huge(con: Any, path: Path) -> int:
    """Écrit dans `path` les lignes de la vue des morceaux énormes de la table `parts` (`_huge_rows`), par lots que la
    mémoire de la connexion contient ; leur nombre (0 : rien d'écrit)."""
    huge = f"FROM parts WHERE dim > 0 AND npoints > {PIECE_VERTICES}"
    ((count, vertices),) = con.execute(f"SELECT count(*), sum(npoints) {huge}").fetchall()  # lu jusqu'au bout
    if not count:
        return 0
    rows = max(1, int(duck.limits("view")[0] * 2**20 // (HUGE_COPIES * 16 * vertices / count)))
    workers = duck.machine_threads()
    writer = None
    try:
        with (
            con.sql(f"SELECT fid, esize, psize, ST_AsWKB(p) AS wkb {huge}").to_arrow_reader(rows) as reader,
            ThreadPoolExecutor(workers, thread_name_prefix="etl-view") as pool,
        ):
            for batch in reader:
                fid, esize, psize = (batch.column(c).to_numpy() for c in ("fid", "esize", "psize"))
                g = shapely.from_wkb(batch.column("wkb").to_numpy(zero_copy_only=False))
                table = _huge_rows(fid, esize, psize, g, pool, workers)
                if table is not None:
                    writer = writer or pq.ParquetWriter(path, table.schema, compression="zstd")
                    writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    return int(count) if writer is not None else 0


def _levels_sql() -> str:
    """Morceaux généralisés de chaque niveau (requête sur la table `parts`) : fid, esize, lvl, role, s, bbox. Les
    morceaux énormes n'y sont que par leurs cellules : le reste est calculé en Python (`_huge_rows`)."""
    selects = []
    for lv in LEVELS:
        cell, tolerance, dot = pixel(lv), pixel(lv + 2), pixel(lv + 1)
        selects.append(
            f"""SELECT fid, esize, {lv} AS lvl, {PART} AS role, s,
                       {_bbox("ST_XMin(s)", "ST_YMin(s)", "ST_XMax(s)", "ST_YMax(s)")} AS bbox
                FROM (SELECT fid, esize, ST_Simplify(p, {tolerance}) AS s FROM parts
                      WHERE dim > 0 AND psize >= {cell} AND npoints <= {PIECE_VERTICES})
                WHERE NOT ST_IsEmpty(s)"""
        )
        # morceaux de moins d'un pixel : un point par entité et par cellule, sans géométrie (la cellule suffit)
        selects.append(
            f"""SELECT fid, any_value(esize) AS esize, {lv} AS lvl, {PART} AS role, NULL::GEOMETRY AS s,
                       {_bbox(f"i * {dot}", f"j * {dot}", f"(i + 1) * {dot}", f"(j + 1) * {dot}")} AS bbox
                FROM (SELECT fid, esize, floor((xmin + xmax) / 2 / {dot})::BIGINT AS i,
                             floor((ymin + ymax) / 2 / {dot})::BIGINT AS j
                      FROM parts WHERE dim > 0 AND psize < {cell})
                GROUP BY fid, i, j"""
        )
    return " UNION ALL ".join(selects)


def build_view(port_file: Path) -> dict[str, Any] | None:
    """Écrit la vue du port `port_file` (à côté de lui, même s'il désigne un fichier ailleurs) ; ses métadonnées."""
    src = port_data(port_file)
    if src is None:
        return None
    schema = pq.ParquetFile(src).schema_arrow
    crs = get_crs(schema)
    if GEOM not in schema.names or not crs:
        return None

    dst, ents = view_path(port_file), entities_path(port_file)
    tag = uuid.uuid4().hex[:8]
    tmp, tmp_ents = dst.with_name(f"{dst.name}.{tag}.tmp"), ents.with_name(f"{ents.name}.{tag}.tmp")
    tmp_huge = dst.with_name(f"{dst.name}.{tag}.huge.tmp")
    con = duck.connect("view")  # la mémoire de l'inspecteur (pas celle des runs), tous les cœurs : on l'attend
    steps: dict[str, float] = {}
    started = clock = time.perf_counter()

    def step(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        steps[name], clock = round(now - clock, 2), now

    try:
        duck.load_spatial(con)
        # reprojection (le plus cher) une seule fois, puis éclatement en morceaux
        con.execute(
            f"""
            CREATE TEMP TABLE ents AS
            SELECT fid, kind, g, greatest(ST_XMax(g) - ST_XMin(g), ST_YMax(g) - ST_YMin(g)) AS esize
            FROM (SELECT fid, ST_GeometryType(g0)::VARCHAR AS kind,
                         ST_Transform(g0, {_lit(crs)}, {_lit(WEB_MERCATOR)}, true) AS g
                  FROM (SELECT file_row_number AS fid, ST_GeomFromWKB({GEOM}) AS g0
                        FROM read_parquet({_lit(src.as_posix())}, file_row_number=true)))
            """
        )
        step("reprojection")
        con.execute(
            f"""
            COPY (
              SELECT fid, kind, ST_NPoints(g) AS npoints, esize,
                     {_bbox("ST_XMin(g)", "ST_YMin(g)", "ST_XMax(g)", "ST_YMax(g)")} AS bbox
              FROM ents
              ORDER BY {_hilbert("(ST_XMin(g) + ST_XMax(g)) / 2", "(ST_YMin(g) + ST_YMax(g)) / 2")} NULLS LAST
            ) TO {_lit(tmp_ents.as_posix())} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})
            """
        )
        entities, types = con.execute(
            "SELECT count(*), list(DISTINCT kind) FILTER (WHERE kind IS NOT NULL) FROM ents"
        ).fetchone()
        step("index")
        con.execute(
            """
            CREATE TEMP TABLE parts AS
            SELECT fid, esize, p, xmin, ymin, xmax, ymax, greatest(xmax - xmin, ymax - ymin) AS psize, dim, npoints
            FROM (SELECT fid, esize, geom AS p, ST_XMin(geom) AS xmin, ST_YMin(geom) AS ymin,
                         ST_XMax(geom) AS xmax, ST_YMax(geom) AS ymax, ST_Dimension(geom) AS dim,
                         ST_NPoints(geom) AS npoints
                  FROM (SELECT fid, esize, unnest(ST_Dump(g), recursive := true) FROM ents)
                  WHERE NOT ST_IsEmpty(geom))
            """
        )
        con.execute("DROP TABLE ents")
        step("morceaux")
        huge = _write_huge(con, tmp_huge)
        step("morceaux énormes")
        extra = f"UNION ALL SELECT * FROM read_parquet({_lit(tmp_huge.as_posix())})" if huge else ""
        con.execute(
            f"""
            COPY (
              SELECT fid, lvl, esize, role::UTINYINT AS role, bbox, geom_3857
              FROM (SELECT fid, lvl, esize, role, bbox, ST_AsWKB(s) AS geom_3857
                    FROM (SELECT fid, esize, CASE WHEN dim = 0 THEN {POINTS} ELSE {FULL} END AS lvl, {PART} AS role,
                                 p AS s, {_bbox("xmin", "ymin", "xmax", "ymax")} AS bbox
                          FROM parts WHERE npoints <= {PIECE_VERTICES}
                          UNION ALL {_levels_sql()})
                    {extra})
              ORDER BY lvl, {_hilbert("(bbox.xmin + bbox.xmax) / 2", "(bbox.ymin + bbox.ymax) / 2")}
            ) TO {_lit(tmp.as_posix())} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE},
                                         ROW_GROUP_SIZE_BYTES '{ROW_GROUP_BYTES}')
            """
        )
        step("niveaux et écriture")
        xmin, ymin, xmax, ymax = con.execute("SELECT min(xmin), min(ymin), max(xmax), max(ymax) FROM parts").fetchone()
        if xmin is None:
            return None  # aucune géométrie exploitable
        tmp.replace(dst)
        tmp_ents.replace(ents)
    finally:
        con.close()
        tmp.unlink(missing_ok=True)
        tmp_ents.unlink(missing_ok=True)
        tmp_huge.unlink(missing_ok=True)
    log.info(
        "carte de %s : %d entités, %d morceaux énormes, %.1f s %s",
        port_file.name,
        entities,
        huge,
        time.perf_counter() - started,
        steps,
    )
    meta = {
        "version": VERSION,
        "crs": crs,
        "rows": int(entities),
        "geometry_types": sorted(str(t) for t in (types or [])),
        "extent_3857": [float(xmin), float(ymin), float(xmax), float(ymax)],
    }
    view_meta_path(port_file).write_text(json.dumps(meta), encoding="utf-8")
    return meta


def _discard_others(port_file: Path) -> None:
    """Efface les vues périmées du même port : autre version, ou vues des « N premières » d'avant (une vue en cours
    de lecture reste : Windows la verrouille)."""
    for f in port_file.parent.glob(f"{_stem(port_file)}.view*"):
        if not f.name.endswith(".tmp"):
            with contextlib.suppress(OSError):
                f.unlink()
