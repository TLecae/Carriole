"""Formats du jalon 5 (hors PostgreSQL) : Shapefile, MapInfo, Excel, GeoParquet partitionné, Reader sur motif."""

import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import formats
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.formats import ogr
from etl_engine.modifiers.reader import Reader
from etl_engine.modifiers.writer import Writer
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier
from test_formats import make_table, roundtrip


def polygons(n: int = 6, multi_every: int = 3) -> pa.Table:
    geoms = []
    for i in range(n):
        poly = shapely.box(i * 10, 0, i * 10 + 5, 5)
        geoms.append(shapely.MultiPolygon([poly]) if i % multi_every == 0 else poly)
    t = pa.table({"id": pa.array(range(n), pa.int64()), GEOM: pa.array(shapely.to_wkb(geoms), pa.binary())})
    return with_crs(t, "EPSG:2154")


# ------------------------------------------------------------------------------------------ Shapefile


def test_shapefile_roundtrip_keeps_accents_and_writes_cpg(tmp_path):
    fmt, path, got, schema = roundtrip(tmp_path, "s.shp", make_table())
    assert fmt == "shp" and got.num_rows == 10
    assert got.column("nom").to_pylist() == [f"é{i}" for i in range(10)]
    assert (tmp_path / "s.cpg").read_text().strip().upper() == "UTF-8"
    assert "2154" in get_crs(schema)


def test_shapefile_names_truncated_unique_and_reported():
    names, renamed = ogr.shapefile_names(["identifiant_long", "identifiant_long2", "identifiant_long3", "id", GEOM])
    assert all(len(n) <= 10 for n in names)
    assert len({n.lower() for n in names}) == len(names)
    assert names[3:] == ["id", GEOM]
    assert set(renamed) == {"identifiant_long", "identifiant_long2", "identifiant_long3"}


def test_shapefile_writer_logs_renames(tmp_path):
    t = make_table().append_column("colonne_tres_longue", pa.array(range(10)))
    writer = formats.open_writer("shp", str(tmp_path / "r.shp"))
    writer.write(t)
    writer.close()
    assert any("colonne_tres_longue → colonne_tr" in m for m in writer.messages)
    back = pa.concat_tables(formats.read_batches("shp", str(tmp_path / "r.shp"), 100))
    assert "colonne_tr" in back.column_names


def test_shapefile_mixed_polygons_and_multipolygons(tmp_path):
    _, _, got, _ = roundtrip(tmp_path, "p.shp", polygons())
    assert got.num_rows == 6
    kinds = {shapely.from_wkb(w).geom_type for w in got.column(GEOM).to_pylist()}
    assert kinds <= {"Polygon", "MultiPolygon"}


def test_shapefile_size_limit_message(tmp_path, monkeypatch):
    monkeypatch.setattr(ogr, "SHP_MAX_BYTES", 100)
    writer = formats.open_writer("shp", str(tmp_path / "big.shp"))
    with pytest.raises(ConfigError, match="2 Go"):
        writer.write(make_table(50))


# ------------------------------------------------------------------------------------------ MapInfo


@pytest.mark.parametrize("name", ["m.tab", "m.mif"])
def test_mapinfo_roundtrip(tmp_path, name):
    src = make_table()
    _, path, got, schema = roundtrip(tmp_path, name, src)
    assert got.num_rows == 10
    assert got.column("nom").to_pylist() == src.column("nom").to_pylist()
    # MapInfo stocke des entiers dans ses limites : précision de l'ordre du centimètre en Lambert-93
    assert shapely.from_wkb(got.column(GEOM).to_pylist()[2]).distance(shapely.Point(700002, 6500002)) < 0.05
    assert os.path.exists(path)
    assert get_crs(schema)


# ------------------------------------------------------------------------------------------- Excel


def test_xlsx_roundtrip_types(tmp_path):
    import datetime as dt

    t = pa.table(
        {
            "id": pa.array([1, 2, 3]),
            "nom": pa.array(["a", None, "c"]),
            "val": pa.array([1.5, 2.0, None]),
            "ok": pa.array([True, False, True]),
            "jour": pa.array([dt.datetime(2026, 1, 2, 3, 4), None, dt.datetime(2026, 5, 6)]),
        }
    )
    _, _, got, schema = roundtrip(tmp_path, "t.xlsx", t)
    assert schema.field("id").type == pa.int64() and schema.field("val").type == pa.float64()
    assert schema.field("ok").type == pa.bool_()
    assert got.column("nom").to_pylist() == ["a", None, "c"]
    assert got.column("jour").to_pylist()[0] == dt.datetime(2026, 1, 2, 3, 4)


def test_xlsx_geometry_written_as_wkt(tmp_path):
    _, _, got, _ = roundtrip(tmp_path, "g.xlsx", make_table(4))
    assert got.column("geometry").to_pylist()[1].startswith("POINT")


def test_xlsx_several_sheets_in_one_workbook(tmp_path):
    path = str(tmp_path / "multi.xlsx")
    for sheet, rows in (("communes", 3), ("lieux", 5)):
        w = formats.open_writer("xlsx", path, layer=sheet, mode="overwrite")
        w.write(pa.table({"n": pa.array(range(rows))}))
        w.close()
    from etl_engine.formats import xlsx

    assert xlsx.sheets(path) == ["communes", "lieux"]
    for sheet, rows in (("communes", 3), ("lieux", 5)):
        assert pa.concat_tables(formats.read_batches("xlsx", path, 10, layer=sheet)).num_rows == rows
    # overwrite ne remplace que la feuille visée
    w = formats.open_writer("xlsx", path, layer="communes", mode="overwrite")
    w.write(pa.table({"n": pa.array([9])}))
    w.close()
    assert pa.concat_tables(formats.read_batches("xlsx", path, 10, layer="communes")).num_rows == 1
    assert pa.concat_tables(formats.read_batches("xlsx", path, 10, layer="lieux")).num_rows == 5
    # append ajoute à la suite
    w = formats.open_writer("xlsx", path, layer="lieux", mode="append")
    w.write(pa.table({"n": pa.array([7, 8])}))
    w.close()
    assert pa.concat_tables(formats.read_batches("xlsx", path, 10, layer="lieux")).num_rows == 7


def test_xlsx_range_header_and_unknown_sheet(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["titre du tableau"])
    ws.append(["code", "nb", "code"])
    ws.append(["01", 5, "x"])
    ws.append(["02", 6.5, "y"])
    path = str(tmp_path / "r.xlsx")
    wb.save(path)

    got = pa.concat_tables(formats.read_batches("xlsx", path, 10, layer="data", cell_range="A2:C4"))
    assert got.column_names == ["code", "nb", "code_1"]
    assert got.column("code").to_pylist() == ["01", "02"]  # le texte reste du texte
    assert got.column("nb").to_pylist() == [5.0, 6.5]  # entier + flottant → flottant
    with pytest.raises(ConfigError, match="introuvable"):
        list(formats.read_batches("xlsx", path, 10, layer="absente"))
    with pytest.raises(ConfigError, match="plage invalide"):
        list(formats.read_batches("xlsx", path, 10, cell_range="pas une plage"))


def test_xlsx_row_limit(tmp_path, monkeypatch):
    from etl_engine.formats import xlsx

    monkeypatch.setattr(xlsx, "MAX_ROWS", 5)
    w = formats.open_writer("xlsx", str(tmp_path / "l.xlsx"))
    with pytest.raises(ConfigError, match="limite"):
        w.write(pa.table({"n": pa.array(range(10))}))
    w.abort()


# --------------------------------------------------------------------------------- GeoParquet


def test_parquet_partitioned_write_and_read_back(tmp_path):
    src = make_table(12).append_column("dep", pa.array(["31", "09", None] * 4))
    out = str(tmp_path / "part")
    w = formats.open_writer("parquet", out, partition_by=["dep"], compression="snappy")
    w.write(src.slice(0, 6))
    w.write(src.slice(6))
    w.close()
    folders = sorted(os.listdir(out))
    assert folders == ["dep=09", "dep=31", "dep=__null__"]
    total = 0
    for folder in folders:
        f = pq.ParquetFile(os.path.join(out, folder, "part-0.parquet"))
        assert f.metadata.row_group(0).column(0).compression == "SNAPPY"
        batches = list(formats.read_batches("parquet", os.path.join(out, folder, "part-0.parquet"), 100))
        total += sum(b.num_rows for b in batches)
        assert "2154" in get_crs(batches[0])
    assert total == 12


def test_parquet_partition_column_missing(tmp_path):
    w = formats.open_writer("parquet", str(tmp_path / "p"), partition_by=["absente"])
    with pytest.raises(ValueError, match="absente"):
        w.write(make_table(3))


# ------------------------------------------------------------------------------------- Reader


def test_reader_pattern_reads_all_files_with_filename(tmp_path):
    for name in ("a", "b"):
        w = formats.open_writer("gpkg", str(tmp_path / f"{name}.gpkg"))
        w.write(make_table(4))
        w.close()
    out = run_modifier(Reader, {"path": str(tmp_path / "*.gpkg"), "add_filename": True})["output"]
    assert out.num_rows == 8
    assert sorted(set(out.column("filename").to_pylist())) == ["a.gpkg", "b.gpkg"]


def test_reader_and_writer_xlsx_sheet(tmp_path):
    path = str(tmp_path / "x.xlsx")
    run_modifier(Writer, {"path": path, "layer": "feuille A"}, pa.table({"n": pa.array([1, 2])}))
    out = run_modifier(Reader, {"path": path, "layer": "feuille A"})["output"]
    assert out.column("n").to_pylist() == [1, 2]


def test_writer_logs_shapefile_rename(tmp_path):
    t = make_table(3).append_column("une_colonne_longue", pa.array([1, 2, 3]))
    seen: list[str] = []
    from etl_engine.sdk import Context

    ctx = Context(log_sink=lambda _n, _lvl, msg: seen.append(msg))
    run_modifier(Writer, {"path": str(tmp_path / "w.shp")}, t, ctx=ctx)
    assert any("raccourci" in m for m in seen)
