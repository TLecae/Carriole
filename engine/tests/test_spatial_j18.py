"""Jalon 18 : noyau spatial (grande géométrie préparée, découpes locales, erreurs isolées) et Clip qui s'en sert."""

import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.modifiers.clip import Clip
from etl_engine.sdk import Context
from etl_engine.spatial import PREDICATES, SpatialIndex, keep_dimension, overlay
from etl_engine.testing import run_modifier


def shapes(geoms, **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    wkb = [None if g is None else shapely.to_wkb(g) for g in geoms]
    return with_crs(table.append_column(GEOM, pa.array(wkb, pa.binary())), "EPSG:2154")


def jagged(n=6000, radius=1000.0, seed=3):
    """Un « département » : polygone dentelé de `n` sommets (bien plus que les seuils de préparation et de carreaux)."""
    rng = np.random.default_rng(seed)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = radius * (1 + 0.08 * np.sin(angles * 37) + 0.02 * rng.standard_normal(n))
    return shapely.Polygon(np.c_[r * np.cos(angles), r * np.sin(angles)])


def parcels(n=4000, seed=5, extent=1300.0):
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(-extent, extent, n), rng.uniform(-extent, extent, n)
    w, h = rng.uniform(5, 60, n), rng.uniform(5, 60, n)
    return shapely.box(x, y, x + w, y + h)


@pytest.mark.parametrize("predicate", PREDICATES)
def test_pairs_are_those_of_a_plain_strtree(predicate):
    refs = np.array([jagged(), shapely.box(0, 0, 50, 50), jagged(800, 300, 7)], dtype=object)
    cands = np.concatenate([parcels(), [None, jagged(2000, 400, 9)]])
    expected = shapely.STRtree(refs).query(
        cands, predicate=predicate, distance=25.0 if predicate == "dwithin" else None
    )
    order = np.lexsort((expected[1], expected[0]))
    i, j = SpatialIndex.of(refs).pairs(cands, predicate, 25.0)
    assert np.array_equal(i, expected[0][order]) and np.array_equal(j, expected[1][order])


def test_local_pieces_cut_like_the_whole_geometry():
    big = jagged()
    index = SpatialIndex.of(np.array([big], dtype=object))
    cands = parcels(1500)
    i, j = index.pairs(cands)
    edge = ~index.test(cands, i, j, "covered_by")  # seules les entités à cheval sont découpées
    i, j = i[edge], j[edge]
    local = index.local(j, shapely.bounds(cands[i]))
    # à cheval sur une coupe, une entité reçoit le carreau parent ; sur une coupe centrale, la géométrie entière
    # (exact, juste plus lent) : c'est rare
    sizes = shapely.get_num_coordinates(local)
    assert np.median(sizes) <= 300 and (sizes == shapely.get_num_coordinates(big)).mean() < 0.05
    for fn in (shapely.intersection, shapely.difference):
        mine, whole = fn(cands[i], local), fn(cands[i], big)
        assert np.allclose(shapely.area(mine), shapely.area(whole))
        assert shapely.area(shapely.symmetric_difference(mine, whole)).max() < 1e-6


def test_keep_dimension_drops_contacts_and_lower_parts():
    touching = shapely.intersection(shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1))  # un trait
    mixed = shapely.GeometryCollection([shapely.box(0, 0, 1, 1), shapely.LineString([(5, 5), (6, 6)])])
    out = keep_dimension(np.array([touching, mixed, shapely.box(0, 0, 1, 1)], dtype=object), np.array([2, 2, 2]))
    assert out[0] is None and out[1].geom_type == "Polygon" and out[2].area == 1


def test_overlay_isolates_the_pair_geos_refuses():
    def fragile(a, b):
        if np.ndim(a) and len(a) > 1:
            raise shapely.errors.GEOSException("TopologyException: lot entier refusé")
        if a is not None and a.area == 4:
            raise shapely.errors.GEOSException("TopologyException: cette paire")
        return shapely.intersection(a, b)

    a = np.array([shapely.box(0, 0, 1, 1), shapely.box(0, 0, 2, 2), shapely.box(0, 0, 3, 3)], dtype=object)
    out, errors = overlay(fragile, a, np.array([shapely.box(0, 0, 9, 9)] * 3, dtype=object))
    assert list(errors) == [1] and "cette paire" in errors[1]
    assert out[0].area == 1 and out[2].area == 9


@pytest.mark.parametrize("mode", ["union", "each"])
def test_clip_with_a_big_clipper_same_result_in_parallel_and_conserves_area(mode):
    cells = parcels(6000)
    clippee = shapes(list(cells))
    clipper = shapes([jagged()], name=["dep"])
    params = {"mode": mode, "overlay_attributes": ["name"] if mode == "each" else []}
    alone = run_modifier(Clip, params, {"input": clippee, "overlay": clipper}, batch_rows=6000)
    parallel = run_modifier(
        Clip, params, {"input": clippee, "overlay": clipper}, batch_rows=700, ctx=Context(workers=8)
    )
    for port in ("Inside", "Outside"):
        assert alone[port].column("_n").to_pylist() == parallel[port].column("_n").to_pylist()

    def area(t):
        return shapely.area(shapely.from_wkb(t.column(GEOM).to_numpy(zero_copy_only=False))).sum()

    assert area(parallel["Inside"]) + area(parallel["Outside"]) == pytest.approx(shapely.area(cells).sum())
    assert area(parallel["Inside"]) == pytest.approx(shapely.area(shapely.intersection(cells, jagged())).sum())


def test_clip_keeps_untouched_geometries_byte_for_byte_and_drops_contacts():
    inside, cut, touching = shapely.box(1, 1, 2, 2), shapely.box(8, 1, 12, 2), shapely.box(10, 5, 11, 6)
    clippee = shapes([inside, cut, touching, None])
    out = run_modifier(Clip, {}, {"input": clippee, "overlay": shapes([shapely.box(0, 0, 10, 10)])})
    assert out["Inside"].column("_n").to_pylist() == [0, 1]
    assert out["Inside"].column(GEOM)[0].as_py() == clippee.column(GEOM)[0].as_py()  # telle quelle, sans calcul
    assert shapely.from_wkb(out["Inside"].column(GEOM)[1].as_py()).area == 2
    assert out["Outside"].column("_n").to_pylist() == [1, 2]  # le simple contact reste entier dehors
    assert out["Outside"].column(GEOM)[1].as_py() == clippee.column(GEOM)[2].as_py()
    assert out["Rejected"].column("_rejection_reason").to_pylist() == ["pas de géométrie"]
