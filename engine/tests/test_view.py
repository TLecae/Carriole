"""Vue d'inspection : les entités d'un port, prêtes pour les tuiles, construite à la demande."""

import numpy as np
import pyarrow as pa
import pyarrow.compute
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import view as views
from etl_engine.data import GEOM, with_crs
from etl_engine.view import build_view, ensure_view, entities_path, read_view_meta, view_path


def write_points(path, n=2000, crs="EPSG:2154"):
    rng = np.random.default_rng(1)
    xs, ys = rng.uniform(500_000, 700_000, n), rng.uniform(6_300_000, 6_500_000, n)
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array([f"p{i}" for i in range(n)]),
            GEOM: pa.array(shapely.to_wkb(shapely.points(xs, ys)), pa.binary()),
        }
    )
    pq.write_table(with_crs(t, crs), path)
    return xs, ys


def test_view_is_hilbert_sorted_with_bbox_3857_and_fid(tmp_path):
    src = tmp_path / "output.parquet"
    write_points(src)
    meta = build_view(src)
    assert meta["rows"] == 2000 and meta["geometry_types"] == ["POINT"] and meta["crs"] == "EPSG:2154"
    assert read_view_meta(src) == meta

    view = pq.read_table(view_path(src))
    # la carte seule : les attributs restent au port
    assert view.column_names == ["fid", "lvl", "esize", "bbox", "geom_3857"]
    assert set(view.column("esize").to_pylist()) == {0}  # un point n'a pas de taille
    assert set(view.column("lvl").to_pylist()) == {views.POINTS}  # des points : à tous les zooms, sans généralisation
    assert sorted(view.column("fid").to_pylist()) == list(range(2000))  # fid = ligne d'origine, rien perdu
    # le fichier du pipeline n'a pas bougé
    assert pq.read_table(src).column("id").to_pylist() == list(range(2000))

    # Hilbert : des lignes voisines sont proches dans l'espace (bien plus qu'en ordre d'origine)
    def mean_jump(table):
        b = table.column("bbox").combine_chunks()
        x = np.array(b.field("xmin").to_numpy(zero_copy_only=False))
        y = np.array(b.field("ymin").to_numpy(zero_copy_only=False))
        return float(np.mean(np.hypot(np.diff(x), np.diff(y))))

    assert mean_jump(view) < 0.25 * 120_000  # points aléatoires : ~100 km de saut moyen sans tri

    # bbox cohérente avec la géométrie 3857
    row = view.slice(0, 1).to_pylist()[0]
    g = shapely.from_wkb(row["geom_3857"])
    assert (row["bbox"]["xmin"], row["bbox"]["ymin"]) == (g.x, g.y)


def test_no_view_without_geometry_or_crs(tmp_path):
    plain = tmp_path / "plain.parquet"
    pq.write_table(pa.table({"a": [1, 2]}), plain)
    assert build_view(plain) is None and not view_path(plain).exists()

    nocrs = tmp_path / "nocrs.parquet"
    write_points(nocrs, n=10, crs=None)
    assert build_view(nocrs) is None


def test_single_point_dataset_has_a_view(tmp_path):
    src = tmp_path / "one.parquet"
    write_points(src, n=1)
    assert build_view(src)["rows"] == 1


def test_a_ready_view_is_reused_and_older_views_are_discarded(tmp_path):
    src = tmp_path / "output.parquet"
    write_points(src)
    # vues d'avant (« N premières ») ou d'une autre version
    for old in ("output.view-300.parquet", "output.view-all.json", "output.view.json"):
        (tmp_path / old).write_text("{}")
    meta = ensure_view(src)
    assert meta["rows"] == 2000
    stamp = view_path(src).stat().st_mtime_ns
    assert ensure_view(src) == meta and view_path(src).stat().st_mtime_ns == stamp  # déjà prête : relue
    assert sorted(f.name for f in tmp_path.glob("output.view*")) == [
        "output.view.ent.parquet",
        "output.view.json",
        "output.view.parquet",
    ]


def write_dissolved(path, n=4000):
    """Une seule entité : des milliers de parcelles fusionnées (comme un Dissolve sur un département)."""
    rng = np.random.default_rng(2)
    xs, ys = rng.uniform(500_000, 600_000, n), rng.uniform(6_300_000, 6_400_000, n)
    parts = [*shapely.box(xs, ys, xs + 120, ys + 80), shapely.box(520_000, 6_320_000, 560_000, 6_360_000)]
    t = pa.table({"id": [1], GEOM: pa.array([shapely.to_wkb(shapely.multipolygons(parts))], pa.binary())})
    pq.write_table(with_crs(t, "EPSG:2154"), path)


def test_huge_entity_is_split_into_parts_and_generalized_by_zoom(tmp_path):
    src = tmp_path / "Dissolved.parquet"
    write_dissolved(src)
    meta = build_view(src)
    assert meta["rows"] == 1 and meta["geometry_types"] == ["MULTIPOLYGON"]
    view = pq.read_table(view_path(src))
    lvl = np.array(view.column("lvl").to_pylist())
    geom = np.array(view.column("geom_3857").is_null().to_pylist())
    assert (lvl == views.FULL).sum() == 4001  # une ligne par morceau : une tuile ne lit que ceux de son emprise
    # petite échelle : quelques cellules d'un pixel (sans géométrie, la cellule est l'emprise), pas 4 001 polygones
    assert 0 < (lvl == 0).sum() < 50 and geom[lvl == 0].all()
    cell = view.filter(pa.compute.equal(view.column("lvl"), 0)).column("bbox").to_pylist()[0]
    assert cell["xmax"] - cell["xmin"] == pytest.approx(views.pixel(1)) == cell["ymax"] - cell["ymin"]
    assert (lvl == 10).sum() > 3600 and not geom[lvl == 10].any()  # zoom 10 (pixel de 76 m) : des polygones
    (ent,) = pq.read_table(entities_path(src)).to_pylist()
    assert (ent["fid"], ent["kind"], ent["npoints"]) == (0, "MULTIPOLYGON", 4001 * 5)
    b = ent["bbox"]
    assert ent["esize"] == max(b["xmax"] - b["xmin"], b["ymax"] - b["ymin"])  # taille : le plus grand côté
    assert set(view.column("esize").to_pylist()) == {ent["esize"]}  # chaque morceau porte la taille de l'entité
