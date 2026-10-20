import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.modifiers.create_grid import CreateGrid
from etl_engine.modifiers.hull import Hull
from etl_engine.modifiers.join_by_nearest import JoinByNearest
from etl_engine.modifiers.measure_length import MeasureLength
from etl_engine.modifiers.multipart_to_singleparts import MultipartToSingleparts
from etl_engine.modifiers.overlay_polygons import OverlayPolygons
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier


def shapes(geoms, crs="EPSG:2154", **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    table = table.append_column(GEOM, pa.array([None if g is None else shapely.to_wkb(g) for g in geoms], pa.binary()))
    return with_crs(table, crs)


def geoms_of(table):
    return [None if w is None else shapely.from_wkb(w) for w in table.column(GEOM).to_pylist()]


# ------------------------------------------------------------------ MeasureLength
def test_length_of_lines_and_rejection_of_other_geometries():
    data = shapes(
        [
            shapely.LineString([(0, 0), (300, 400)]),
            shapely.MultiLineString([[(0, 0), (10, 0)], [(0, 5), (0, 15)]]),
            shapely.Point(1, 1),
            None,
        ],
        id=[1, 2, 3, 4],
    )
    out = run_modifier(MeasureLength, {"attribute": "len"}, data)
    assert out["output"].column("len").to_pylist() == [500.0, 20.0]
    assert out["Rejected"].num_rows == 2
    km = run_modifier(MeasureLength, {"unit": "km", "decimals": 3}, data)["output"].column("_length").to_pylist()
    assert km == [0.5, 0.02]


def test_length_ellipsoidal_and_planar_in_degrees():
    line = shapes([shapely.LineString([(0, 0), (1, 0)])], crs="EPSG:4326")
    meters = (
        run_modifier(MeasureLength, {"method": "ellipsoidal", "decimals": 0}, line)["output"]
        .column("_length")[0]
        .as_py()
    )
    assert 111_000 < meters < 112_000  # un degré de longitude à l'équateur
    with pytest.raises(ConfigError, match="géographique"):
        run_modifier(MeasureLength, {}, line)


# ------------------------------------------------------------------ MultipartToSingleparts
def test_deaggregator_splits_multi_geometries_and_keeps_attributes():
    multi = shapely.MultiPolygon([shapely.box(0, 0, 1, 1), shapely.box(5, 5, 6, 6)])
    collection = shapely.GeometryCollection([shapely.Point(0, 0), shapely.MultiPoint([(1, 1), (2, 2)])])
    data = shapes([multi, shapely.box(9, 9, 10, 10), collection, None], nom=["m", "s", "c", "v"])
    out = run_modifier(MultipartToSingleparts, {"count_attribute": "_n"}, data)["output"]
    assert out.column("nom").to_pylist() == ["m", "m", "s", "c", "c", "c", "v"]
    assert out.column("_part").to_pylist() == [0, 1, 0, 0, 1, 2, 0] and out.column("_n").to_pylist() == [
        2,
        2,
        1,
        3,
        3,
        3,
        1,
    ]
    assert [g.geom_type if g else None for g in geoms_of(out)] == ["Polygon"] * 3 + ["Point"] * 3 + [None]
    flat = run_modifier(MultipartToSingleparts, {"recursive": False}, shapes([collection]))["output"]
    assert [g.geom_type for g in geoms_of(flat)] == ["Point", "MultiPoint"]
    assert get_crs(out) == "EPSG:2154"


# ------------------------------------------------------------------ Hull
SQUARE_POINTS = points([0.0, 4.0, 4.0, 0.0, 2.0], [0.0, 0.0, 4.0, 4.0, 2.0], g=["a", "a", "a", "a", "a"])


def test_hull_per_feature_and_methods():
    line = shapes([shapely.LineString([(0, 0), (4, 0), (4, 3)])])
    assert shapely.from_wkb(run_modifier(Hull, {"method": "convex"}, line)["output"][GEOM][0].as_py()).area == 6.0
    box = run_modifier(Hull, {"method": "envelope"}, line)["output"]
    assert geoms_of(box)[0].bounds == (0.0, 0.0, 4.0, 3.0)
    rect = run_modifier(Hull, {"method": "rectangle"}, line)["output"]
    assert round(geoms_of(rect)[0].area, 1) == 12.0
    circle = run_modifier(Hull, {"method": "circle"}, shapes([shapely.Point(0, 0), None]))
    assert circle["Rejected"].num_rows == 1


def test_hull_of_groups_and_of_everything():
    data = points(
        [0.0, 4.0, 4.0, 10.0, 12.0, 12.0],
        [0.0, 0.0, 4.0, 10.0, 10.0, 14.0],
        g=["a", "a", "a", "b", "b", "b"],
        v=[1, 2, 3, 4, 5, 6],
    )
    groups = run_modifier(Hull, {"scope": "group", "group_by": ["g"]}, data)["output"]
    assert groups.column("g").to_pylist() == ["a", "b"] and [round(g.area, 1) for g in geoms_of(groups)] == [8.0, 4.0]
    assert groups.column("v").to_pylist() == [1, 4]  # attributs de la première entité du groupe
    everything = run_modifier(Hull, {"scope": "all", "method": "envelope"}, data)["output"]
    assert everything.num_rows == 1 and geoms_of(everything)[0].bounds == (0.0, 0.0, 12.0, 14.0)
    with pytest.raises(ConfigError, match="regroupement"):
        run_modifier(Hull, {"scope": "group"}, data)


# ------------------------------------------------------------------ CreateGrid
def test_square_grid_from_an_extent():
    out = run_modifier(
        CreateGrid, {"xmin": 0, "ymin": 0, "xmax": 250, "ymax": 200, "size": 100, "crs": "EPSG:2154"}, batch_rows=4
    )["output"]
    assert out.num_rows == 6 and out.column("cell_id").to_pylist() == list(range(6)) and get_crs(out) == "EPSG:2154"
    assert all(g.area == 10000.0 for g in geoms_of(out))
    assert out.column("col").to_pylist() == [0, 1, 2, 0, 1, 2] and out.column("row").to_pylist() == [0, 0, 0, 1, 1, 1]


def test_hexagon_grid_covers_the_extent():
    out = run_modifier(CreateGrid, {"xmin": 0, "ymin": 0, "xmax": 1000, "ymax": 1000, "size": 100, "shape": "hexagon"})[
        "output"
    ]
    cells = geoms_of(out)
    assert all(len(g.exterior.coords) == 7 for g in cells)
    assert round(cells[0].area) == round(100**2 * np.sqrt(3) / 2)  # aire d'un hexagone de largeur 100
    union = shapely.union_all(cells)
    assert shapely.box(50, 50, 950, 950).difference(union).area < 1e-6  # aucun trou dans l'emprise


def test_grid_from_incoming_features_and_limits():
    data = shapes([shapely.box(0, 0, 250, 100), shapely.box(1000, 1000, 1010, 1010)])
    full = run_modifier(CreateGrid, {"from_input": True, "size": 500}, data)["output"]
    assert full.num_rows == 9  # emprise 1010 x 1010 : 3 colonnes x 3 lignes de 500
    near = run_modifier(CreateGrid, {"from_input": True, "size": 100, "only_touching": True}, data)["output"]
    assert near.num_rows == 6 + 4 and get_crs(near) == "EPSG:2154"
    assert CreateGrid.ports(CreateGrid.Params.model_validate({"from_input": True})) == (("input",), ("output",))
    assert CreateGrid.ports(CreateGrid.Params.model_validate({})) == ((), ("output",))
    with pytest.raises(ConfigError, match="trop de cellules"):
        run_modifier(CreateGrid, {"xmax": 1e9, "ymax": 1e9, "size": 1})
    with pytest.raises(ConfigError, match="emprise est vide"):
        run_modifier(CreateGrid, {"xmin": 5, "xmax": 5})


# ------------------------------------------------------------------ JoinByNearest
def test_neighbors_nearest_with_rank_and_distance():
    base = points([0.0, 100.0], [0.0, 0.0], nom=["a", "b"])
    cand = points([10.0, 30.0, 95.0, 500.0], [0.0, 0.0, 0.0, 0.0], code=["c10", "c30", "c95", "c500"])
    one = run_modifier(JoinByNearest, {"count": 1}, {"base": base, "candidate": cand})
    assert [(r["nom"], r["near_code"], r["_distance"], r["_rank"]) for r in one["Matched"].to_pylist()] == [
        ("a", "c10", 10.0, 1),
        ("b", "c95", 5.0, 1),
    ]
    three = run_modifier(JoinByNearest, {"count": 3}, {"base": base, "candidate": cand})["Matched"].to_pylist()
    assert [(r["nom"], r["near_code"], r["_rank"]) for r in three] == [
        ("a", "c10", 1),
        ("a", "c30", 2),
        ("a", "c95", 3),
        ("b", "c95", 1),
        ("b", "c30", 2),
        ("b", "c10", 3),
    ]


def test_neighbors_max_distance_and_no_neighbor_and_polygons():
    base = points([0.0, 1000.0], [0.0, 0.0], nom=["a", "b"])
    cand = points([10.0], [0.0], code=["c"])
    out = run_modifier(JoinByNearest, {"max_distance": 50}, {"base": base, "candidate": cand})
    assert out["Matched"].num_rows == 1 and out["NoNeighbor"].column("nom").to_pylist() == ["b"]
    # polygones : la distance est celle des bords, pas des centres
    poly_base = shapes([shapely.box(0, 0, 10, 10)], nom=["p"])
    poly_cand = shapes([shapely.box(30, 0, 40, 10), shapely.box(0, 12, 10, 100)], code=["loin", "près"])
    near = run_modifier(JoinByNearest, {"attributes": ["code"]}, {"base": poly_base, "candidate": poly_cand})[
        "Matched"
    ].to_pylist()
    assert near[0]["near_code"] == "près" and near[0]["_distance"] == 2.0


def test_neighbors_refuse_different_systems_and_reject_empty_geometries():
    a, b = points([0.0], [0.0], nom=["x"]), points([0.0], [0.0], code=["y"], crs="EPSG:4326")
    with pytest.raises(ConfigError, match="système de coordonnées"):
        run_modifier(JoinByNearest, {}, {"base": a, "candidate": b})
    nogeo = shapes([None], nom=["z"])
    out = run_modifier(JoinByNearest, {}, {"base": nogeo, "candidate": points([0.0], [0.0], code=["y"])})
    assert out["Rejected"].num_rows == 1


# ------------------------------------------------------------------ OverlayPolygons
def test_overlay_cuts_overlapping_polygons_into_pieces():
    a, b = shapely.box(0, 0, 10, 10), shapely.box(5, 0, 15, 10)
    data = shapes([a, b], nom=["A", "B"])
    out = run_modifier(OverlayPolygons, {"attributes": ["nom"]}, data)["output"]
    pieces = sorted(
        zip(
            out.column("_overlap_count").to_pylist(),
            [round(g.area) for g in geoms_of(out)],
            out.column("nom").to_pylist(),
            strict=True,
        )
    )
    assert pieces == [(1, 50, ["A"]), (1, 50, ["B"]), (2, 50, ["A", "B"])]
    assert sorted(map(tuple, out.column("_origins").to_pylist())) == [(0,), (0, 1), (1,)]
    conflicts = run_modifier(OverlayPolygons, {"min_overlap": 2}, data)["output"]
    assert conflicts.num_rows == 1 and round(geoms_of(conflicts)[0].area) == 50


def test_overlay_ignores_holes_and_rejects_non_polygons():
    ring = shapely.Polygon([(0, 0), (30, 0), (30, 30), (0, 30)], holes=[[(10, 10), (20, 10), (20, 20), (10, 20)]])
    hole_filler = shapely.box(12, 12, 18, 18)
    data = shapes([ring, hole_filler, shapely.Point(1, 1)], nom=["ring", "fill", "pt"])
    out = run_modifier(OverlayPolygons, {}, data)
    assert out["Rejected"].num_rows == 1
    total = sum(g.area for g in geoms_of(out["output"]))
    assert round(total) == round(ring.area + hole_filler.area)  # le trou n'est comblé que par le petit polygone
    assert out["output"].column("_overlap_count").to_pylist().count(1) == out["output"].num_rows
