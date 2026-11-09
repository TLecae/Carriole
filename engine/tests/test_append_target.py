"""Ajout à une couche existante (GeoPackage, Shapefile, CSV, Excel) : les attributs qu'elle ne peut pas recevoir sont
nommés dans l'éditeur et arrêtent le run avant toute lecture ; CSV et Excel rangent chaque attribut dans sa colonne."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import formats
from etl_engine.analysis import analyze
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow
from etl_engine.field_values import writer_target
from etl_engine.formats import databases
from etl_engine.sdk import ConfigError
from openpyxl import Workbook, load_workbook

from conftest import make_flow


def points(**columns):
    n = len(next(iter(columns.values())))
    wkb = shapely.to_wkb([shapely.Point(i, i) for i in range(n)])
    return with_crs(pa.table({**columns, GEOM: pa.array(wkb, pa.binary())}), "EPSG:2154")


def write(fmt, path, table, **opts):
    writer = formats.open_writer(fmt, str(path), **opts)
    writer.write(table)
    writer.close()


def test_a_field_absent_from_the_geopackage_layer_is_named_before_writing(tmp_path):
    target = tmp_path / "sortie.gpkg"
    write("gpkg", target, points(insee=["31555"], nom=["Toulouse"]), layer="communes", mode="overwrite")
    renamed = points(insee__=["31069"], NOM=["Blagnac"])  # casse indifférente, comme GDAL
    with pytest.raises(ConfigError, match="attributs absents de la couche « communes » : insee__$"):
        formats.check_append("gpkg", str(target), renamed.schema, "communes")
    with pytest.raises(ConfigError, match="couche « communes » : insee__$"):  # au run aussi, sans l'erreur de GDAL
        write("gpkg", target, renamed, layer="communes", mode="append")
    formats.check_append("gpkg", str(target), renamed.schema, "autre")  # couche absente : l'ajout la crée
    formats.check_append("gpkg", str(tmp_path / "nouveau.gpkg"), renamed.schema)
    write("gpkg", target, points(insee=["31069"]), layer="communes", mode="append")  # nom vide : laissé vide
    assert formats.target_columns("gpkg", str(target), "communes") == [
        {"name": "insee", "type": "object"},
        {"name": "nom", "type": "object"},
    ]


def test_the_layer_of_a_geopackage_without_a_name_is_the_file_name(tmp_path):
    target = tmp_path / "communes.gpkg"
    write("gpkg", target, points(insee=["31555"]), mode="overwrite")
    with pytest.raises(ConfigError, match="couche « communes » : nom"):
        formats.check_append("gpkg", str(target), points(nom=["x"]).schema)


def test_a_shapefile_is_compared_with_its_shortened_names(tmp_path):
    target = tmp_path / "communes.shp"
    write("shp", target, points(population_2020=[1]), mode="overwrite")
    formats.check_append("shp", str(target), points(population_2020=[2]).schema)
    with pytest.raises(ConfigError, match="couche « communes » : nom"):
        formats.check_append("shp", str(target), points(population_2020=[2], nom=["x"]).schema)


def test_a_csv_append_follows_the_header_of_the_file(tmp_path):
    target = tmp_path / "communes.csv"
    target.write_bytes(b"nom;insee;population\nToulouse;31555;504078")  # sans fin de ligne
    write("csv", target, pa.table({"insee": ["31069"], "nom": ["Blagnac"]}), delimiter=";", mode="append")
    assert target.read_bytes().decode().splitlines() == [
        "nom;insee;population",
        "Toulouse;31555;504078",
        '"Blagnac";"31069";',
    ]
    with pytest.raises(ConfigError, match="attributs absents de l'en-tête du fichier : code$"):
        write("csv", target, pa.table({"code": ["x"]}), delimiter=";", mode="append")
    created = tmp_path / "nouveau.csv"  # ajout à un fichier absent : il est créé avec son en-tête
    write("csv", created, pa.table({"insee": ["31069"]}), mode="append")
    assert created.read_bytes().decode().splitlines() == ['"insee"', '"31069"']


def test_an_excel_append_follows_the_header_of_the_sheet(tmp_path):
    target = tmp_path / "communes.xlsx"
    wb = Workbook()
    wb.active.title = "2024"
    wb.active.append(["nom", "insee", "population"])
    wb.active.append(["Toulouse", "31555", 504078])
    wb.save(target)
    assert formats.target_columns("xlsx", str(target), "2024") == [
        {"name": "nom", "type": ""},
        {"name": "insee", "type": ""},
        {"name": "population", "type": ""},
    ]
    write("xlsx", target, pa.table({"insee": ["31069"], "nom": ["Blagnac"]}), layer="2024", mode="append")
    rows = list(load_workbook(target)["2024"].iter_rows(values_only=True))
    assert rows[-1] == ("Blagnac", "31069", None)
    with pytest.raises(ConfigError, match="attributs absents de la feuille « 2024 » : code$"):
        write("xlsx", target, pa.table({"code": ["x"]}), layer="2024", mode="append")


class FakeBase:
    """Base simulée : aucune connexion réelle."""

    case_sensitive_columns = True
    calls = 0

    def table_columns(self, connection, table):
        FakeBase.calls += 1
        return [{"name": "insee", "type": "text"}, {"name": "geom", "type": "geometry"}] if table == "carto.c" else None


@pytest.mark.parametrize("node_type", ["PostgresWriter", "OracleWriter"])
def test_a_column_absent_from_the_database_table_stops_the_run_before_reading(
    registry, tmp_path, monkeypatch, node_type
):
    monkeypatch.setattr(databases, "backend", lambda connection: FakeBase())
    monkeypatch.setattr(databases, "_columns_seen", {})
    source = tmp_path / "communes.parquet"
    pq.write_table(points(insee=["31069"], nom=["Blagnac"]), source)

    def flow(table, mode="append"):
        params = {"connection": "ma_base", "table": table, "mode": mode}
        nodes = [
            {"id": "read", "type": "GeoParquetReader", "params": {"path": str(source)}},
            {"id": "write", "type": node_type, "params": params},
        ]
        return make_flow(nodes, [("read", "output", "write", "input")])

    res = run_flow(flow("carto.c"), registry, tmp_path / "run", cache=False)
    assert res.status == "error" and res.nodes["read"].status != "done"
    assert res.nodes["write"].error == "colonnes absentes de la table carto.c : nom"  # géométrie : colonne geom
    calls = FakeBase.calls
    issues = analyze(flow("carto.c"), registry)["issues"]
    assert [i["message"] for i in issues] == ["colonnes absentes de la table carto.c : nom"]
    assert FakeBase.calls == calls  # mémorisé : la base n'est pas réinterrogée à chaque analyse
    assert not analyze(flow("carto.c", mode="overwrite"), registry)["issues"]  # recréée : rien à comparer
    assert not analyze(flow("carto.nouvelle"), registry)["issues"]  # créée par l'ajout


def test_the_run_stops_before_reading_and_the_editor_lists_the_layer(registry, tmp_path):
    source = tmp_path / "communes.parquet"
    pq.write_table(points(insee__=["31069"], nom=["Blagnac"]), source)
    target = tmp_path / "sortie.gpkg"
    write("gpkg", target, points(insee=["31555"], nom=["Toulouse"]), layer="communes", mode="overwrite")
    nodes = [
        {"id": "read", "type": "GeoParquetReader", "params": {"path": str(source)}},
        {
            "id": "write",
            "type": "GeoPackageWriter",
            "params": {"path": str(target), "layer": "communes", "mode": "append"},
        },
    ]
    flow = make_flow(nodes, [("read", "output", "write", "input")])
    res = run_flow(flow, registry, tmp_path / "run", cache=False)
    assert res.status == "error"
    assert "attributs absents de la couche « communes » : insee__" in res.nodes["write"].error
    assert res.nodes["read"].status != "done"  # rien n'a été lu
    assert writer_target(flow, registry, "write") == {
        "exists": True,
        "columns": [{"name": "insee", "type": "object"}, {"name": "nom", "type": "object"}],
        "case_sensitive": False,
        "incoming": ["insee__", "nom"],
    }
    nodes[1]["params"]["mode"] = "overwrite"  # hors ajout : la couche visée ne compte pas
    assert writer_target(make_flow(nodes, [("read", "output", "write", "input")]), registry, "write")["exists"] is False
    with pytest.raises(ConfigError, match="n'écrit pas dans un fichier"):
        writer_target(flow, registry, "read")


def test_a_geoparquet_append_keeps_the_rows_of_the_file(tmp_path):
    """Le mode « append » remplaçait le fichier ; « create » ne refusait pas un fichier existant."""
    target = tmp_path / "communes.parquet"
    write("parquet", target, points(insee=["31555", "31069"], pop=[504078, 10000]), mode="overwrite")
    with pytest.raises(ConfigError, match="le fichier existe déjà"):
        formats.open_writer("parquet", str(target), mode="create")
    write("parquet", target, points(pop=[2.0], insee=["31149"]), mode="append")  # ordre et type du fichier
    write("parquet", target, points(insee=["31003"]), mode="append")  # pop : vide
    table = pa.concat_tables(formats.read_batches("parquet", str(target), 1000))
    assert table.column("insee").to_pylist() == ["31555", "31069", "31149", "31003"]
    assert table.column("pop").to_pylist() == [504078, 10000, 2, None]
    assert table.schema.field("pop").type == pa.int64() and get_crs(table) == "EPSG:2154"
    assert [shapely.from_wkb(g).x for g in table.column(GEOM).to_pylist()] == [0, 1, 0, 0]
    with pytest.raises(ConfigError, match="attributs absents du fichier : nom$"):
        formats.check_append("parquet", str(target), points(nom=["x"]).schema)
    with pytest.raises(ConfigError, match="attributs absents du fichier : nom$"):
        write("parquet", target, points(nom=["x"]), mode="append")
    with pytest.raises(ConfigError, match="système de coordonnées différent"):
        write("parquet", target, with_crs(points(insee=["x"]), "EPSG:4326"), mode="append")
    with pytest.raises(ConfigError, match="ne s'écrit pas dans une colonne int64"):
        write("parquet", target, points(pop=["beaucoup"]), mode="append")
    assert pq.read_table(target).num_rows == 4  # erreurs : le fichier est intact


def test_a_partitioned_append_adds_a_file_per_partition(tmp_path):
    target = tmp_path / "communes"
    table = points(dep=["31", "09"], insee=["31555", "09122"])
    write("parquet", target, table, partition_by=["dep"], mode="overwrite")
    write("parquet", target, table, partition_by=["dep"], mode="append")
    assert len(list(target.glob("dep=31/*.parquet"))) == 2
    assert sum(pq.read_metadata(f).num_rows for f in target.glob("*/*.parquet")) == 4
    with pytest.raises(ConfigError, match="le dossier existe déjà"):
        formats.open_writer("parquet", str(target), partition_by=["dep"], mode="create")
