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
  son emprise), pour ne dessiner que les plus grandes d'une vue trop chargée.

Une tuile ne lit que les morceaux de son niveau dans son emprise : une entité énorme (parcelles fusionnées sur une
région : 10 millions de sommets, 300 000 morceaux) ne coûte pas plus qu'un jeu de petites entités. Lignes triées par
niveau puis selon la courbe de Hilbert (morceaux proches dans les mêmes row groups).

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
import threading
import uuid
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from etl_engine import duck
from etl_engine.data import GEOM, get_crs

VERSION = 4  # une vue d'une autre version est reconstruite
ROW_GROUP_SIZE = 50_000
WEB_MERCATOR = "EPSG:3857"
WORLD = 20037508.342789244  # demi-largeur du monde en EPSG:3857
TILE_PIXELS = 512
LEVELS = (0, 2, 4, 6, 8, 10)  # niveaux généralisés : le niveau L sert aux zooms L et L+1
FULL = 99  # morceaux entiers, à partir du zoom FULL_FROM
POINTS = -1  # points, à tous les zooms
FULL_FROM = 12
WORLD_BOX = f"{{'min_x': {-WORLD}, 'min_y': {-WORLD}, 'max_x': {WORLD}, 'max_y': {WORLD}}}::BOX_2D"
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def pixel(z: int) -> float:
    """Taille d'un pixel au zoom `z`, en mètres EPSG:3857 (tuiles de 512 pixels)."""
    return 2 * WORLD / (TILE_PIXELS * 2**z)


def level_for(z: int) -> int:
    """Niveau de la vue lu pour une tuile de zoom `z`."""
    return FULL if z >= FULL_FROM else max(lv for lv in LEVELS if lv <= z)


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


def _levels_sql() -> str:
    """Morceaux généralisés de chaque niveau (requête sur la table `parts`) : fid, esize, lvl, s, bbox."""
    selects = []
    for lv in LEVELS:
        cell, tolerance, dot = pixel(lv), pixel(lv + 2), pixel(lv + 1)
        selects.append(
            f"""SELECT fid, esize, {lv} AS lvl, s,
                       {_bbox("ST_XMin(s)", "ST_YMin(s)", "ST_XMax(s)", "ST_YMax(s)")} AS bbox
                FROM (SELECT fid, esize, ST_Simplify(p, {tolerance}) AS s FROM parts
                      WHERE dim > 0 AND psize >= {cell})
                WHERE NOT ST_IsEmpty(s)"""
        )
        # morceaux de moins d'un pixel : un point par entité et par cellule, sans géométrie (la cellule suffit)
        selects.append(
            f"""SELECT fid, any_value(esize) AS esize, {lv} AS lvl, NULL::GEOMETRY AS s,
                       {_bbox(f"i * {dot}", f"j * {dot}", f"(i + 1) * {dot}", f"(j + 1) * {dot}")} AS bbox
                FROM (SELECT fid, esize, floor((xmin + xmax) / 2 / {dot})::BIGINT AS i,
                             floor((ymin + ymax) / 2 / {dot})::BIGINT AS j
                      FROM parts WHERE dim > 0 AND psize < {cell})
                GROUP BY fid, i, j"""
        )
    return " UNION ALL ".join(selects)


def build_view(src: Path) -> dict[str, Any] | None:
    """Écrit la vue de `src` et renvoie ses métadonnées."""
    schema = pq.ParquetFile(src).schema_arrow
    crs = get_crs(schema)
    if GEOM not in schema.names or not crs:
        return None

    dst, ents = view_path(src), entities_path(src)
    tag = uuid.uuid4().hex[:8]
    tmp, tmp_ents = dst.with_name(f"{dst.name}.{tag}.tmp"), ents.with_name(f"{ents.name}.{tag}.tmp")
    con = duck.connect("inspect")  # l'inspecteur ne doit pas prendre la mémoire des runs
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
        con.execute(
            """
            CREATE TEMP TABLE parts AS
            SELECT fid, esize, p, xmin, ymin, xmax, ymax, greatest(xmax - xmin, ymax - ymin) AS psize, dim
            FROM (SELECT fid, esize, geom AS p, ST_XMin(geom) AS xmin, ST_YMin(geom) AS ymin,
                         ST_XMax(geom) AS xmax, ST_YMax(geom) AS ymax, ST_Dimension(geom) AS dim
                  FROM (SELECT fid, esize, unnest(ST_Dump(g), recursive := true) FROM ents)
                  WHERE NOT ST_IsEmpty(geom))
            """
        )
        con.execute("DROP TABLE ents")
        con.execute(
            f"""
            COPY (
              SELECT fid, lvl, esize, bbox, ST_AsWKB(s) AS geom_3857
              FROM (SELECT fid, esize, CASE WHEN dim = 0 THEN {POINTS} ELSE {FULL} END AS lvl, p AS s,
                           {_bbox("xmin", "ymin", "xmax", "ymax")} AS bbox
                    FROM parts
                    UNION ALL {_levels_sql()})
              ORDER BY lvl, {_hilbert("(bbox.xmin + bbox.xmax) / 2", "(bbox.ymin + bbox.ymax) / 2")}
            ) TO {_lit(tmp.as_posix())} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})
            """
        )
        xmin, ymin, xmax, ymax = con.execute("SELECT min(xmin), min(ymin), max(xmax), max(ymax) FROM parts").fetchone()
        if xmin is None:
            return None  # aucune géométrie exploitable
        tmp.replace(dst)
        tmp_ents.replace(ents)
    finally:
        con.close()
        tmp.unlink(missing_ok=True)
        tmp_ents.unlink(missing_ok=True)
    meta = {
        "version": VERSION,
        "crs": crs,
        "rows": int(entities),
        "geometry_types": sorted(str(t) for t in (types or [])),
        "extent_3857": [float(xmin), float(ymin), float(xmax), float(ymax)],
    }
    view_meta_path(src).write_text(json.dumps(meta), encoding="utf-8")
    return meta


def _discard_others(port_file: Path) -> None:
    """Efface les vues périmées du même port : autre version, ou vues des « N premières » d'avant (une vue en cours
    de lecture reste : Windows la verrouille)."""
    for f in port_file.parent.glob(f"{_stem(port_file)}.view*"):
        if not f.name.endswith(".tmp"):
            with contextlib.suppress(OSError):
                f.unlink()
