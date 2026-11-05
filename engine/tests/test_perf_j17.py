"""Jalon 17 : traitements par paquets et en parallèle, mêmes résultats qu'un calcul d'un seul tenant."""

import threading
import time

import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine import duck
from etl_engine.blocking import group_codes, group_slices
from etl_engine.data import GEOM, with_crs
from etl_engine.modifiers import dissolve, overlay_polygons
from etl_engine.modifiers.clip import Clip
from etl_engine.modifiers.dissolve import Dissolve
from etl_engine.modifiers.overlay_polygons import OverlayPolygons
from etl_engine.sdk import Context, ordered_map
from etl_engine.testing import run_modifier


def shapes(geoms, **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    table = table.append_column(GEOM, pa.array(shapely.to_wkb(np.asarray(geoms, dtype=object)), pa.binary()))
    return with_crs(table, "EPSG:2154")


def disks(n, seed=1, extent=1000.0, radius=60.0):
    rng = np.random.default_rng(seed)
    centers = shapely.points(rng.uniform(0, extent, n), rng.uniform(0, extent, n))
    return shapely.buffer(centers, radius * rng.uniform(0.5, 1.5, n), quad_segs=4)


def pieces_signature(table):
    """Morceaux comparables d'un calcul à l'autre : (origines, surface, centre), triés (sommets dans un autre ordre)."""
    geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
    cent = shapely.centroid(geoms)
    return sorted(
        (tuple(o), round(float(a), 4), round(float(x), 3), round(float(y), 3))
        for o, a, x, y in zip(
            table.column("_origins").to_pylist(),
            shapely.area(geoms),
            shapely.get_x(cent),
            shapely.get_y(cent),
            strict=True,
        )
    )


# ------------------------------------------------------------------ lots en parallèle, dans l'ordre
def test_ordered_map_keeps_order_and_runs_in_parallel():
    seen = set()

    def work(i):
        seen.add(threading.get_ident())
        time.sleep(0.02 * ((i * 7) % 3))
        return i * i

    assert list(ordered_map(work, range(30), 4)) == [i * i for i in range(30)]
    assert len(seen) > 1


def test_ordered_map_propagates_errors_and_cancellation():
    def boom(i):
        if i == 5:
            raise ValueError("cinq")
        return i

    with pytest.raises(ValueError, match="cinq"):
        list(ordered_map(boom, range(20), 3))
    ctx = Context()
    ctx.cancel.set()
    from etl_engine.sdk import Cancelled

    with pytest.raises(Cancelled):
        list(ordered_map(lambda i: i, range(5), 2, ctx))


# ------------------------------------------------------------------ regroupement vectorisé
def test_group_codes_by_first_appearance_with_nulls():
    t = pa.table({"a": ["x", "y", None, "x", None, "y"], "b": [1, 1, 2, 1, 2, 2]})
    codes, count = group_codes(t, ["a", "b"])
    assert codes.tolist() == [0, 1, 2, 0, 2, 3] and count == 4
    order, bounds = group_slices(codes, count)
    assert [order[bounds[g] : bounds[g + 1]].tolist() for g in range(count)] == [[0, 3], [1], [2, 4], [5]]
    codes, count = group_codes(t, [])
    assert codes.tolist() == [0] * 6 and count == 1


def test_group_codes_on_list_columns_falls_back():
    t = pa.table({"l": [[1, 2], [3], [1, 2]]})
    codes, count = group_codes(t, ["l"])
    assert codes.tolist() == [0, 1, 0] and count == 2


# ------------------------------------------------------------------ superposition par carreaux : exacte, sans doublon
def test_overlay_by_tiles_equals_a_single_tile(monkeypatch):
    """Faces entières de chaque carreau, et faces à cheval recollées exactement : mêmes morceaux qu'en un seul carreau.
    Un grand polygone qui traverse tous les carreaux (un réseau d'un seul tenant) redevient une face."""
    cross = shapely.union_all(
        [shapely.LineString([(0, 500), (1000, 520)]), shapely.LineString([(500, 0), (480, 1000)])]
    ).buffer(8, quad_segs=16)
    geoms = [*disks(240), cross]
    data = shapes(geoms, kind=[f"k{i % 3}" for i in range(len(geoms))])
    params = {"attributes": ["kind"], "min_overlap": 1}
    whole = run_modifier(OverlayPolygons, params, data, batch_rows=50)["output"]  # un seul carreau
    monkeypatch.setattr(overlay_polygons, "MIN_TILE_VERTICES", 100)  # beaucoup de carreaux, des faces à cheval
    monkeypatch.setattr(overlay_polygons, "TILES_PER_WORKER", 64)
    assert len(overlay_polygons._Overlay(np.asarray(geoms, dtype=object), 1, 4, None).tiles) > 20
    tiled = run_modifier(OverlayPolygons, params, data, batch_rows=50, ctx=Context(workers=4))["output"]
    assert tiled.num_rows == whole.num_rows > 240
    assert pieces_signature(tiled) == pieces_signature(whole)
    # les listes d'attributs suivent les origines
    kinds = data.column("kind").to_pylist()
    for origins, values in zip(tiled.column("_origins").to_pylist(), tiled.column("kind").to_pylist(), strict=True):
        assert values == [kinds[o] for o in origins]
    # la somme des morceaux couvre exactement l'union des polygones, sans morceau invalide
    pieces = shapely.from_wkb(tiled.column(GEOM).to_numpy(zero_copy_only=False))
    assert shapely.is_valid(pieces).all()
    assert shapely.area(pieces).sum() == pytest.approx(shapely.union_all(geoms).area, rel=1e-9)
    alone = [o for o in tiled.column("_origins").to_pylist() if o == [len(geoms) - 1]]
    assert len(alone) == len([o for o in whole.column("_origins").to_pylist() if o == [len(geoms) - 1]])


# ------------------------------------------------------------------ Dissolve : union en arbre
def test_dissolve_tree_union_equals_single_union(monkeypatch):
    cells = shapely.box(*np.array([(x, y, x + 10, y + 10) for x in range(0, 300, 10) for y in range(0, 200, 10)]).T)
    data = shapes(cells, g=["a" if i % 7 else "b" for i in range(len(cells))])
    whole = run_modifier(Dissolve, {}, data, batch_rows=100)["output"]
    monkeypatch.setattr(dissolve, "CHUNK", 25)
    parts = run_modifier(Dissolve, {}, data, batch_rows=100, ctx=Context(workers=4))["output"]
    a = shapely.from_wkb(whole.column(GEOM)[0].as_py())
    b = shapely.from_wkb(parts.column(GEOM)[0].as_py())
    assert shapely.equals(a, b) and b.area == pytest.approx(300 * 200)
    grouped = run_modifier(Dissolve, {"group_by": ["g"]}, data, ctx=Context(workers=4))["output"]
    assert grouped.column("g").to_pylist() == ["b", "a"] and grouped.column("_count").to_pylist() == [86, 514]


# ------------------------------------------------------------------ Clip en flux
def test_clip_streams_clippees_in_order():
    cells = shapely.box(*np.array([(x, 0, x + 10, 10) for x in range(0, 100, 10)]).T)
    clippers = shapes([shapely.box(15, -5, 45, 15), shapely.box(42, -5, 72, 15)], name=["A", "B"])
    out = run_modifier(
        Clip,
        {"mode": "each", "overlay_attributes": ["name"]},
        {"input": shapes(cells), "overlay": clippers},
        batch_rows=2,
        ctx=Context(workers=3),
    )
    inside = out["Inside"]
    assert inside.column("_n").to_pylist() == [1, 2, 3, 4, 4, 5, 6, 7]
    assert inside.column("overlay_name").to_pylist() == ["A", "A", "A", "A", "B", "B", "B", "B"]
    assert out["Outside"].column("_n").to_pylist() == [0, 1, 7, 8, 9]


# ------------------------------------------------------------------ DuckDB borné
def test_duck_connections_get_a_share_of_the_run_budget(tmp_path):
    duck.configure_run(memory_mb=4000, threads=8, spill_dir=tmp_path)
    try:
        con = duck.connect("expr")
        limit = con.execute("SELECT current_setting('memory_limit'), current_setting('threads')").fetchone()
        assert limit[1] == 1 and limit[0].endswith(("MiB", "MB", "GiB", "GB"))
        heavy = duck.connect("heavy").execute("SELECT current_setting('threads')").fetchone()[0]
        assert heavy == 8
        assert duck.limits("heavy")[0] == int(4000 * 0.6 / duck.heavy_slots())
        duck.configure_heavy_nodes(1)  # un seul nœud lourd dans le run : toute la part de DuckDB
        assert duck.limits("heavy")[0] == int(4000 * 0.6)
        duck.configure_heavy_nodes(5)  # plus que de places : partagée entre les nœuds qui tournent ensemble
        assert duck.limits("heavy")[0] == int(4000 * 0.6 / duck.heavy_slots())
    finally:
        duck.configure_run()


def test_batches_take_their_share_of_the_run_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_BATCH_WORKERS", "4")
    try:
        duck.configure_run(memory_mb=8000, threads=8, spill_dir=tmp_path)
        assert duck.batch_bytes() == int(8000 * 0.4 * 2**20 / (2 * 4 * 4))  # 100 Mo
        duck.configure_run(memory_mb=512, threads=8, spill_dir=tmp_path)
        assert duck.batch_bytes() == 16 * 2**20  # jamais de lots minuscules
    finally:
        duck.configure_run()


def test_tile_pieces_whose_seam_does_not_match_are_joined_by_a_full_union():
    """Un sommet de limite déplacé (calcul d'intersection difficile) : la réunion de couverture rendrait une géométrie
    invalide sans erreur ; la réunion complète prend le relais."""
    left = shapely.box(0, 0, 1, 1)
    exact, moved = shapely.box(1, 0, 2, 1), shapely.Polygon([(1, 0), (2, 0), (2, 1), (1, 1.0000000001)])
    for right in (exact, moved):
        joined = overlay_polygons._join(np.array([left, right]), np.array([[0.0, 0, 1, 1], [1, 0, 2, 1]]))
        assert joined.geom_type == "Polygon" and shapely.is_valid(joined)
        assert joined.area == pytest.approx(2.0)
