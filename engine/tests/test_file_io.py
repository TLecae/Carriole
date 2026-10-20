"""Un lecteur et un écrivain par format : leurs seuls réglages, mêmes résultats que l'ancien lecteur générique."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.flow import Flow
from etl_engine.modifiers.file_io import (
    CsvReader,
    CsvWriter,
    ExcelReader,
    ExcelWriter,
    FileReader,
    FileWriter,
    GeoPackageReader,
    GeoPackageWriter,
    GeoParquetReader,
    GeoParquetWriter,
    ShapefileWriter,
    SpatiaLiteReader,
    SpatiaLiteWriter,
    convert_legacy_node,
)
from etl_engine.registry import Registry
from etl_engine.sdk import first_rows
from etl_engine.testing import run_modifier


def sample():
    t = pa.table({"code": ["031", "009"], "pop": [10, 20]})
    wkb = shapely.to_wkb(shapely.points([1.0, 2.0], [3.0, 4.0]))
    return with_crs(t.append_column(GEOM, pa.array(wkb, pa.binary())), "EPSG:2154")


def write(cls, params, table):
    run_modifier(cls, params, {"input": table})


def read(cls, params):
    return run_modifier(cls, params, {})["output"]


def test_each_format_has_only_its_own_settings():
    fields = {cls.name: set(cls.Params.model_fields) for cls in (CsvReader, GeoPackageReader, ExcelReader)}
    assert {"delimiter", "wkt_column", "x_column"} <= fields["CsvReader"] and "layer" not in fields["CsvReader"]
    assert {"layer", "where"} <= fields["GeoPackageReader"] and "delimiter" not in fields["GeoPackageReader"]
    assert "sheet" in fields["ExcelReader"] and "wkt_column" not in fields["ExcelReader"]
    assert list(CsvReader.Params.model_fields)[0] == "path"  # le chemin d'abord dans le formulaire
    schema = CsvReader.Params.model_json_schema()["properties"]["path"]
    assert schema["x-etl-extensions"] == [".csv", ".txt", ".tsv"] and schema["x-etl-widget"] == "path"
    assert GeoPackageWriter.Params.model_json_schema()["properties"]["path"]["x-etl-access"] == "write"


def test_bases_and_former_nodes_stay_out_of_the_palette():
    registry = Registry.discover()
    assert FileReader.name == FileWriter.name == "" and "CsvReader" in registry and "GeoParquetWriter" in registry
    assert registry.get("Reader").hidden and registry.get("Writer").hidden and not registry.get("CsvReader").hidden
    assert registry.get("CsvReader").describe()["category"] == "Lecture"


@pytest.mark.parametrize(
    "writer, reader, name, extra",
    [
        (GeoPackageWriter, GeoPackageReader, "out.gpkg", {"layer": "communes"}),
        (GeoParquetWriter, GeoParquetReader, "out.parquet", {}),
        (SpatiaLiteWriter, SpatiaLiteReader, "out.sqlite", {"layer": "communes"}),
    ],
)
def test_write_then_read_back_keeps_values_geometry_and_crs(tmp_path, writer, reader, name, extra):
    path = str(tmp_path / name)
    write(writer, {"path": path, **extra}, sample())
    back = read(reader, {"path": path, **extra})
    assert back.column("code").to_pylist() == ["031", "009"] and get_crs(back) == "EPSG:2154"
    assert [g.x for g in geometries(back)] == [1.0, 2.0]


def test_csv_round_trip_with_wkt_and_text_codes(tmp_path):
    path = str(tmp_path / "out.csv")
    write(CsvWriter, {"path": path, "delimiter": ";"}, sample())
    back = read(CsvReader, {"path": path, "delimiter": ";", "wkt_column": "wkt", "crs": "EPSG:2154"})
    assert back.column("code").to_pylist() == ["031", "009"]  # zéros initiaux conservés
    assert geometries(back)[1].equals(shapely.Point(2, 4))


def test_every_reader_can_read_only_the_first_rows(tmp_path):
    """« Lignes lues au plus » : sur tous les lecteurs de la palette ; la limite porte sur tous les fichiers lus."""
    readers = [cls for cls in Registry.discover().all() if cls.category == "Lecture" and not cls.hidden]
    assert len(readers) >= 11
    for cls in readers:
        schema = cls.Params.model_json_schema()["properties"]["max_rows"]
        assert schema["default"] == 0 and schema["x-etl-row-limit"], cls.name

    for i in range(3):  # trois fichiers de 2 entités : 5 entités lues, la lecture s'arrête dans le troisième
        write(GeoParquetWriter, {"path": str(tmp_path / f"part{i}.parquet")}, sample())
    pattern = str(tmp_path / "part*.parquet")
    assert read(GeoParquetReader, {"path": pattern}).num_rows == 6
    assert read(GeoParquetReader, {"path": pattern, "max_rows": 5}).num_rows == 5
    assert read(GeoParquetReader, {"path": pattern, "max_rows": 1}).column("code").to_pylist() == ["031"]
    with pytest.raises(ValueError):
        CsvReader.Params(path="a.csv", max_rows=-1)


def test_first_rows_closes_the_source_it_stops_reading():
    closed = []

    def source():
        try:
            for n in (3, 3, 3):
                yield pa.table({"a": list(range(n))})
        finally:
            closed.append(True)

    assert [t.num_rows for t in first_rows(source(), 4)] == [3, 1] and closed == [True]
    assert [t.num_rows for t in first_rows(source(), 0)] == [3, 3, 3]


def test_excel_sheet_and_shapefile_writer(tmp_path):
    xlsx = str(tmp_path / "out.xlsx")
    write(ExcelWriter, {"path": xlsx, "sheet": "pop"}, sample().drop([GEOM]))
    assert read(ExcelReader, {"path": xlsx, "sheet": "pop"}).column("pop").to_pylist() == [10, 20]
    shp = tmp_path / "out.shp"
    write(ShapefileWriter, {"path": str(shp)}, sample())
    assert shp.is_file() and (tmp_path / "out.cpg").is_file()


def test_former_reader_and_writer_nodes_are_converted_with_their_settings():
    reader = {
        "id": "r",
        "type": "Reader",
        "params": {"path": "D:/a.csv", "delimiter": ";", "layer": "x", "format": "auto"},
    }
    assert convert_legacy_node(reader) == {
        "id": "r",
        "type": "CsvReader",
        "params": {"path": "D:/a.csv", "delimiter": ";"},
    }
    excel = {"id": "w", "type": "Writer", "params": {"path": "D:/o.xlsx", "layer": "f1", "compression": "zstd"}}
    assert convert_legacy_node(excel)["params"] == {"path": "D:/o.xlsx", "sheet": "f1"}
    explicit = {"id": "w", "type": "Writer", "params": {"path": "${OUT}", "format": "gpkg", "layer": "c"}}
    assert convert_legacy_node(explicit)["type"] == "GeoPackageWriter"
    unknown = {"id": "r2", "type": "Reader", "params": {"path": "${SRC}"}}
    assert convert_legacy_node(unknown) is unknown  # format inconnu : l'ancien nœud reste (il fonctionne toujours)
    flow = Flow.model_validate({"nodes": [reader, unknown], "links": []})
    assert [n.type for n in flow.nodes] == ["CsvReader", "Reader"]


def test_former_reader_still_runs(tmp_path):
    path = tmp_path / "a.parquet"
    pq.write_table(sample(), path)
    from etl_engine.modifiers.reader import Reader

    assert run_modifier(Reader, {"path": str(path)}, {})["output"].num_rows == 2


def test_spatialite_tables_overwrite_one_and_sql_query(tmp_path):
    """Une base SpatiaLite garde ses autres tables quand on en remplace une ; une requête SQL remplace la table."""
    path = str(tmp_path / "base.sqlite")
    write(SpatiaLiteWriter, {"path": path, "layer": "communes"}, sample())
    write(SpatiaLiteWriter, {"path": path, "layer": "autre"}, sample())
    write(SpatiaLiteWriter, {"path": path, "layer": "communes", "mode": "overwrite"}, sample().slice(0, 1))
    assert read(SpatiaLiteReader, {"path": path, "layer": "autre"}).num_rows == 2
    assert read(SpatiaLiteReader, {"path": path, "layer": "communes"}).num_rows == 1
    out = read(SpatiaLiteReader, {"path": path, "sql": "SELECT code, geometry FROM autre WHERE pop > 15"})
    assert out.column("code").to_pylist() == ["009"] and [g.x for g in geometries(out)] == [2.0]
    import sqlite3

    with sqlite3.connect(path) as con:  # une vraie base SpatiaLite : ses métadonnées géométriques
        assert con.execute("SELECT COUNT(*) FROM geometry_columns").fetchone()[0] == 2
