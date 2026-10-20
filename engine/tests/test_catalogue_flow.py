"""Le catalogue dans de vrais flux : plusieurs entrées, schémas annoncés = schémas produits."""

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from etl_engine.analysis import analyze
from etl_engine.data import GEOM, with_crs
from etl_engine.executor import run_flow

from conftest import make_flow


def schema_names(report, node, port):
    return [f["name"] for f in report["schemas"][f"{node}/{port}"]["fields"]]


def test_join_statistics_back_onto_features_and_schemas_match(registry, communes, tmp_path):
    out = tmp_path / "joined.parquet"
    flow = make_flow(
        [
            {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
            {
                "id": "stats",
                "type": "StatisticsByGroup",
                "params": {"group_by": ["dep"], "attributes": ["pop"], "functions": ["sum"], "attach": False},
            },
            {
                "id": "join",
                "type": "JoinByField",
                "params": {"base_keys": ["dep"], "join_keys": ["dep"], "attributes": ["pop_sum"]},
            },
            {"id": "sort", "type": "OrderBy", "params": {"keys": [{"attribute": "pop", "order": "descending"}]}},
            {"id": "write", "type": "Writer", "params": {"path": str(out)}},
        ],
        [
            ("read", "output", "stats", "input"),
            ("read", "output", "join", "base"),
            ("stats", "summary", "join", "join"),
            ("join", "Joined", "sort", "input"),
            ("sort", "output", "write", "input"),
        ],
    )
    report = analyze(flow, registry)
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "done", res.error
    got = pq.read_table(out)
    assert got.num_rows == 20 and got.column("pop").to_pylist()[0] == 1900  # trié par population décroissante
    assert set(got.column("pop_sum").to_pylist()) == {
        sum(i * 100 for i in range(0, 20, 2)),
        sum(i * 100 for i in range(1, 20, 2)),
    }
    # le schéma annoncé à l'éditeur est celui qui est réellement produit
    produced = pq.read_table(tmp_path / "run" / "join" / "Joined.parquet").schema.names
    announced = schema_names(report, "join", "Joined")
    assert set(announced) == {n for n in produced if n != GEOM}  # la géométrie est décrite à part
    assert announced == [n for n in produced if n in announced]  # même ordre

    assert report["schemas"]["join/Joined"]["geometry"] is (GEOM in produced)


def test_spatial_pipeline_filter_buffer_dissolve(registry, tmp_path):
    n = 10
    pts = shapely.points(np.arange(n, dtype=float) * 10, np.zeros(n))
    points = with_crs(
        pa.table({"id": pa.array(range(n)), GEOM: pa.array(shapely.to_wkb(pts), pa.binary())}), "EPSG:2154"
    )
    zone = with_crs(
        pa.table({"nom": ["z"], GEOM: pa.array([shapely.to_wkb(shapely.box(-5, -5, 45, 5))], pa.binary())}), "EPSG:2154"
    )
    pq.write_table(points, tmp_path / "pts.parquet")
    pq.write_table(zone, tmp_path / "zone.parquet")
    out = tmp_path / "result.parquet"
    flow = make_flow(
        [
            {"id": "pts", "type": "Reader", "params": {"path": str(tmp_path / "pts.parquet")}},
            {"id": "zone", "type": "Reader", "params": {"path": str(tmp_path / "zone.parquet")}},
            {"id": "filter", "type": "ExtractByLocation", "params": {"predicate": "within"}},
            {"id": "buffer", "type": "Buffer", "params": {"distance": 6}},
            {"id": "dissolve", "type": "Dissolve", "params": {}},
            {"id": "area", "type": "MeasureArea", "params": {"area": "surface", "area_unit": "ha"}},
            {"id": "write", "type": "Writer", "params": {"path": str(out)}},
        ],
        [
            ("pts", "output", "filter", "candidate"),
            ("zone", "output", "filter", "filter"),
            ("filter", "Passed", "buffer", "input"),
            ("buffer", "output", "dissolve", "input"),
            ("dissolve", "output", "area", "input"),
            ("area", "output", "write", "input"),
        ],
    )
    report = analyze(flow, registry)
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert res.nodes["filter"].rows["Passed"] == 5 and res.nodes["filter"].rows["Failed"] == 5
    got = pq.read_table(out)
    assert got.num_rows == 1 and got.column("_count").to_pylist() == [5]
    assert 0 < got.column("surface").to_pylist()[0] < 1  # quelques centaines de m² en hectares


def test_catalogue_page_is_up_to_date(registry):
    import pathlib

    from etl_engine.catalogue import render

    page = pathlib.Path(__file__).resolve().parents[2] / "docs" / "catalogue.md"
    if not page.exists():  # installation sans le dossier docs/
        return
    assert page.read_bytes().decode("utf-8") == render(registry), "régénérer : etl modifiers doc"
