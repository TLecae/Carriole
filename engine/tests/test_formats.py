import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine import formats
from etl_engine.data import GEOM, get_crs, with_crs


def make_table(n: int = 10, crs: str | None = "EPSG:2154") -> pa.Table:
    pts = shapely.points(np.arange(n, dtype=float) + 700000, np.arange(n, dtype=float) + 6500000)
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array([f"é{i}" for i in range(n)]),
            "val": pa.array([i / 2 for i in range(n)], pa.float64()),
            GEOM: pa.array(shapely.to_wkb(pts), pa.binary()),
        }
    )
    return with_crs(t, crs)


def roundtrip(tmp_path, name: str, table: pa.Table, **write_opts):
    path = str(tmp_path / name)
    fmt = formats.detect(path)
    w = formats.open_writer(fmt, path, **write_opts)
    half = table.num_rows // 2
    w.write(table.slice(0, half))
    w.write(table.slice(half))
    w.close()
    batches = list(formats.read_batches(fmt, path, 4))
    return fmt, path, pa.concat_tables(batches), formats.read_schema(fmt, path)


@pytest.mark.parametrize("name", ["a.parquet", "a.gpkg"])
def test_vector_roundtrip(tmp_path, name):
    src = make_table()
    _, _, got, schema = roundtrip(tmp_path, name, src)
    assert got.num_rows == 10
    assert sorted(n for n in got.column_names if n != "fid") == sorted(src.column_names)
    assert got.column("nom").to_pylist() == src.column("nom").to_pylist()
    assert got.column("val").to_pylist() == src.column("val").to_pylist()
    assert shapely.from_wkb(got.column(GEOM).to_pylist()[3]).equals(shapely.Point(700003, 6500003))
    assert get_crs(schema) is not None and "2154" in get_crs(schema)


def test_parquet_geo_metadata(tmp_path):
    import json

    import pyarrow.parquet as pq

    _, path, _, _ = roundtrip(tmp_path, "g.parquet", make_table())
    geo = json.loads(pq.ParquetFile(path).metadata.metadata[b"geo"])
    col = geo["columns"]["geometry"]
    assert geo["version"] == "1.1.0" and col["encoding"] == "WKB" and col["geometry_types"] == ["Point"]
    assert col["bbox"] == [700000.0, 6500000.0, 700009.0, 6500009.0]


def test_csv_roundtrip_wkt_and_xy(tmp_path):
    src = make_table()
    path = str(tmp_path / "t.csv")
    w = formats.open_writer("csv", path)
    w.write(src)
    w.close()
    back = pa.concat_tables(formats.read_batches("csv", path, 100, wkt_column="wkt", crs="EPSG:2154"))
    assert back.num_rows == 10 and GEOM in back.column_names
    assert back.column("id").to_pylist() == [str(i) for i in range(10)]  # texte par défaut : « 007 » reste « 007 »
    assert back.column("nom").to_pylist()[1] == "é1"

    xy = tmp_path / "xy.csv"
    xy.write_text("a;x;y\n1;1,5;2,5\n2;3;4\n", encoding="utf-8")
    got = pa.concat_tables(formats.read_batches("csv", str(xy), 10, x_column="x", y_column="y", crs="EPSG:4326"))
    assert shapely.from_wkb(got.column(GEOM)[0].as_py()).equals(shapely.Point(1.5, 2.5))
    assert get_crs(got) == "EPSG:4326"


def test_csv_detects_latin1(tmp_path):
    p = tmp_path / "l.csv"
    p.write_bytes("nom;ville\nAnaïs;Béziers\n".encode("latin-1"))
    got = pa.concat_tables(formats.read_batches("csv", str(p), 10))
    assert got.column("ville").to_pylist() == ["Béziers"]


def test_gpkg_two_layers_and_append(tmp_path):
    path = str(tmp_path / "m.gpkg")
    src = make_table(4)
    for layer in ("a", "b"):
        w = formats.open_writer("gpkg", path, layer=layer)
        w.write(src)
        w.close()
    w = formats.open_writer("gpkg", path, layer="a", mode="append")
    w.write(src)
    w.close()
    from etl_engine.formats import ogr

    assert sorted(ogr.layers(path)) == ["a", "b"]
    assert sum(t.num_rows for t in formats.read_batches("gpkg", path, 10, layer="a")) == 8
    assert sum(t.num_rows for t in formats.read_batches("gpkg", path, 10, layer="b")) == 4
