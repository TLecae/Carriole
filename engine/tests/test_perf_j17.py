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


# ------------------------------------------------------------------ superposition par paquets : exacte, sans doublon
def test_overlay_by_chunks_equals_single_chunk(monkeypatch):
    data = shapes(disks(240), kind=[f"k{i % 3}" for i in range(240)])
    params = {"attributes": ["kind"], "min_overlap": 1}
    whole = run_modifier(OverlayPolygons, params, data, batch_rows=50)["output"]
    monkeypatch.setattr(overlay_polygons, "CHUNK", 17)  # beaucoup de paquets, des morceaux à cheval
    chunked = run_modifier(OverlayPolygons, params, data, batch_rows=50, ctx=Context(workers=4))["output"]
    assert chunked.num_rows == whole.num_rows > 240
    assert pieces_signature(chunked) == pieces_signature(whole)
    # les listes d'attributs suivent les origines
    kinds = data.column("kind").to_pylist()
    for origins, values in zip(chunked.column("_origins").to_pylist(), chunked.column("kind").to_pylist(), strict=True):
        assert values == [kinds[o] for o in origins]
    # la somme des morceaux couvre exactement l'union des disques
    pieces = shapely.from_wkb(chunked.column(GEOM).to_numpy(zero_copy_only=False))
    assert shapely.area(pieces).sum() == pytest.approx(shapely.union_all(disks(240)).area, rel=1e-9)


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
    finally:
        duck.configure_run()
