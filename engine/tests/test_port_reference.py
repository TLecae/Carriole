"""Port qui désigne un fichier (ADR 0043) : un Parquet déjà au format des ports est lu sur place, sans copie."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import view
from etl_engine.cache import SourceChanged, materialize, port_data, port_path, ref_path
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow
from etl_engine.formats.parquet import ParquetWriter

from conftest import make_flow


def reader(path, **params):
    return {"id": "r", "type": "GeoParquetReader", "params": {"path": str(path), **params}}


def port_table(run_dir, nid, port="output"):
    return pq.read_table(materialize(run_dir, nid, port))


@pytest.fixture
def plain(tmp_path):
    path = tmp_path / "plain.parquet"
    pq.write_table(pa.table({"code": ["01", "02", "03"], "n": [1, 2, 3]}), path)
    return path


@pytest.fixture
def geo(tmp_path):
    """GeoParquet écrit par Carriole : géométrie `geometry`, SCR."""
    path = tmp_path / "geo.parquet"
    table = pa.table({"n": [1, 2], GEOM: shapely.to_wkb([shapely.Point(700000, 6600000), shapely.Point(1, 2)])})
    writer = ParquetWriter(str(path))
    writer.write(with_crs(table, "EPSG:2154"))
    writer.close()
    return path


def test_plain_parquet_is_read_in_place(registry, plain, tmp_path):
    events = []
    flow = make_flow([reader(plain)], [])
    res = run_flow(flow, registry, tmp_path / "run", cache=False, on_event=events.append)
    assert res.status == "done" and res.nodes["r"].rows == {"output": 3}
    slot = port_path(tmp_path / "run", "r", "output")
    assert not slot.exists() and json.loads(ref_path(slot).read_text())["path"] == os.path.abspath(plain)
    assert "fichier lu sur place, sans copie" in [e.get("message") for e in events]
    assert port_table(tmp_path / "run", "r").equals(pq.read_table(plain))


def test_consumers_read_the_file_and_never_delete_it(registry, plain, tmp_path):
    """Sans cache, le port d'un nœud part quand ses consommateurs ont fini, et que plus aucune définition gardée ne le
    désigne : la référence, jamais le fichier."""
    nodes = [
        reader(plain),
        {"id": "sql", "type": "ExtractByExpression", "params": {"conditions": ['"n" >= 2']}},
        {"id": "py", "type": "AddAttributes", "params": {"attributes": [{"name": "m", "expression": '"n" * 10'}]}},
    ]
    flow = make_flow(nodes, [("r", "output", "sql", "input"), ("r", "output", "py", "input")])
    res = run_flow(flow, registry, tmp_path / "run", cache=False, sql_route=True)
    assert res.status == "done", res.error
    assert port_table(tmp_path / "run", "sql", "Passed").column("n").to_pylist() == [2, 3]
    assert port_table(tmp_path / "run", "py").column("m").to_pylist() == [10, 20, 30]
    assert plain.is_file()


def test_geoparquet_keeps_its_crs(registry, geo, tmp_path):
    nodes = [reader(geo), {"id": "p", "type": "Reproject", "params": {"target": "EPSG:4326"}}]
    res = run_flow(make_flow(nodes, [("r", "output", "p", "input")]), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert ref_path(port_path(tmp_path / "run", "r", "output")).is_file()
    out = port_table(tmp_path / "run", "p")
    assert get_crs(out) == "EPSG:4326"
    assert shapely.from_wkb(out.column(GEOM)[0].as_py()).x == pytest.approx(3.0)


def test_geoparquet_crs_from_geo_metadata_only(registry, tmp_path):
    """GeoParquet d'un autre outil : SCR dans les seules métadonnées « geo »."""
    path = tmp_path / "other.parquet"
    geo = {"version": "1.1.0", "primary_column": GEOM, "columns": {GEOM: {"encoding": "WKB", "crs": None}}}
    table = pa.table({"n": [1], GEOM: shapely.to_wkb([shapely.Point(1, 2)])})
    pq.write_table(table.replace_schema_metadata({"geo": json.dumps(geo)}), path)
    nodes = [reader(path), {"id": "p", "type": "Reproject", "params": {"target": "EPSG:2154"}}]
    res = run_flow(make_flow(nodes, [("r", "output", "p", "input")]), registry, tmp_path / "run")
    assert res.status == "done", res.error  # sans SCR, la reprojection refuserait de partir
    assert ref_path(port_path(tmp_path / "run", "r", "output")).is_file()
    out = port_table(tmp_path / "run", "p")
    assert get_crs(out) == "EPSG:2154" and shapely.from_wkb(out.column(GEOM)[0].as_py()).x > 100_000


def test_geoparquet_geometry_stays_wkb_in_expressions(registry, tmp_path):
    """Expressions sur un GeoParquet d'un autre outil, lu sur place : géométrie WKB et SCR comme un port écrit."""
    path = tmp_path / "other.parquet"
    geo = {"version": "1.1.0", "primary_column": GEOM, "columns": {GEOM: {"encoding": "WKB", "crs": None}}}
    table = pa.table({"n": [1, 2], GEOM: shapely.to_wkb([shapely.Point(1, 2), shapely.Point(3, 4)])})
    pq.write_table(table.replace_schema_metadata({"geo": json.dumps(geo)}), path)
    expression = {"name": "x", "expression": "ST_X(geometry)"}
    nodes = [reader(path), {"id": "a", "type": "AddAttributes", "params": {"attributes": [expression]}}]
    res = run_flow(make_flow(nodes, [("r", "output", "a", "input")]), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert port_table(tmp_path / "run", "a").column("x").to_pylist() == [1.0, 3.0]


@pytest.mark.parametrize(
    "params",
    [{"add_filename": True}, {"max_rows": 2}],
    ids=["nom du fichier", "lignes max"],
)
def test_changed_rows_or_columns_are_copied(registry, plain, tmp_path, params):
    res = run_flow(make_flow([reader(plain, **params)], []), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert port_path(tmp_path / "run", "r", "output").is_file()


def test_renamed_geometry_is_copied(registry, tmp_path):
    path = tmp_path / "geom.parquet"
    geo = {"version": "1.1.0", "primary_column": "geom", "columns": {"geom": {"encoding": "WKB"}}}
    table = pa.table({"geom": shapely.to_wkb([shapely.Point(1, 2)])})
    pq.write_table(table.replace_schema_metadata({"geo": json.dumps(geo)}), path)
    res = run_flow(make_flow([reader(path)], []), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert GEOM in pq.read_schema(port_path(tmp_path / "run", "r", "output")).names


def test_changed_source_is_not_taken_for_the_port(registry, plain, tmp_path):
    first = tmp_path / "run1"
    assert run_flow(make_flow([reader(plain)], []), registry, first).status == "done"
    pq.write_table(pa.table({"code": ["09"], "n": [9]}), plain)
    with pytest.raises(SourceChanged):
        port_data(port_path(first, "r", "output"))
    res = run_flow(make_flow([reader(plain)], []), registry, tmp_path / "run2", reuse_from=first)
    assert res.nodes["r"].status == "done" and res.nodes["r"].rows == {"output": 1}


def test_reused_reference(registry, plain, tmp_path):
    first = tmp_path / "run1"
    nodes = [reader(plain), {"id": "sql", "type": "ExtractByExpression", "params": {"conditions": ['"n" > 1']}}]
    flow = make_flow(nodes, [("r", "output", "sql", "input")])
    assert run_flow(flow, registry, first).status == "done"
    nodes[1]["params"]["conditions"] = ['"n" > 2']
    res = run_flow(make_flow(nodes, [("r", "output", "sql", "input")]), registry, tmp_path / "run2", reuse_from=first)
    assert res.nodes["r"].status == "cached" and res.nodes["sql"].status == "done"
    assert port_table(tmp_path / "run2", "sql", "Passed").column("n").to_pylist() == [3]


def test_map_view_is_built_in_the_run_not_next_to_the_file(registry, geo, tmp_path):
    assert run_flow(make_flow([reader(geo)], []), registry, tmp_path / "run").status == "done"
    slot = port_path(tmp_path / "run", "r", "output")
    meta = view.ensure_view(slot)
    assert meta["rows"] == 2 and meta["crs"] == "EPSG:2154"
    assert view.view_path(slot).is_file() and sorted(p.name for p in geo.parent.glob("geo*")) == ["geo.parquet"]
