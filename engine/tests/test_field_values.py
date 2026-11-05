"""Valeurs d'un attribut pour l'aide à la saisie des éditeurs : celles du fichier qu'un lecteur lit."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from etl_engine.data import GEOM, with_crs
from etl_engine.field_values import reader_columns, reader_count, reader_values
from etl_engine.flow import Flow
from etl_engine.sdk import ConfigError


def flow_of(params, published=()):
    return Flow.model_validate(
        {
            "published_params": list(published),
            "nodes": [{"id": "r", "type": "GeoParquetReader", "params": params}],
            "links": [],
        }
    )


def test_most_frequent_values_of_the_file_before_the_reader_filter(registry, tmp_path):
    pq.write_table(
        pa.table({"dep": ["31", "09", "31", "32", "31", None], "n": [1, 2, 3, 4, 5, 6]}), tmp_path / "c.parquet"
    )
    flow = flow_of(
        {"path": "${DOSSIER}/c.parquet", "where": "dep = '09'"}, [{"name": "DOSSIER", "default": str(tmp_path)}]
    )
    got = reader_values(flow, registry, "r", "dep")
    assert got[0] == {"value": "31", "count": 3}  # le filtre du lecteur ne s'applique pas : on choisit ce qu'il garde
    assert {"value": None, "count": 1} in got and len(got) == 4
    assert reader_values(flow, registry, "r", "dep", search="3") == [
        {"value": "31", "count": 3},
        {"value": "32", "count": 1},
    ]
    assert reader_values(flow, registry, "r", "n", limit=2)[0]["count"] == 1


def test_values_refused_for_an_unknown_column_or_a_non_reader(registry, tmp_path):
    pq.write_table(pa.table({"dep": ["31"]}), tmp_path / "c.parquet")
    with pytest.raises(ConfigError, match="inconnue"):
        reader_values(flow_of({"path": str(tmp_path / "c.parquet")}), registry, "r", "inconnue")
    with pytest.raises(ConfigError, match="CSV ou Parquet"):
        reader_values(flow_of({"path": str(tmp_path / "c.parquet")}), registry, "absent", "dep")


def test_columns_of_the_file_before_the_chosen_attributes(registry, tmp_path):
    table = pa.table({"dep": ["31"], "n": [1], GEOM: [b"\x01\x01\x00\x00\x00" + bytes(16)]})
    pq.write_table(with_crs(table, "EPSG:2154"), tmp_path / "c.parquet")
    got = reader_columns(flow_of({"path": str(tmp_path / "c.parquet"), "columns": ["n"]}), registry, "r")
    assert got == [
        {"name": "dep", "type": "string"},
        {"name": "n", "type": "int64"},
        {"name": GEOM, "type": "geometry"},
    ]


def test_count_of_the_entities_kept_by_the_reader_filter(registry, tmp_path):
    """« Compter » de la requête de lecture : toutes les entités, celles que le filtre garde (sans limite de lignes)."""
    pq.write_table(pa.table({"dep": ["31", "09", "31", None]}), tmp_path / "c.parquet")
    path = str(tmp_path / "c.parquet")
    assert reader_count(flow_of({"path": path}), registry, "r") == {"total": 4, "kept": 4}
    assert reader_count(flow_of({"path": path, "where": "dep = '31'", "max_rows": 1}), registry, "r") == {
        "total": 4,
        "kept": 2,
    }
    with pytest.raises(ConfigError, match="comptage impossible"):
        reader_count(flow_of({"path": path, "where": "inconnue = 1"}), registry, "r")
