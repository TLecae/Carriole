"""Lecteurs (Geo)Parquet et CSV par DuckDB (voie SQL, ADR 0042) : même table que la lecture par lots en Python."""

import datetime
import decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.cache import port_data
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow
from etl_engine.flow import Flow


def read_both(registry, tmp_path, kind, params):
    """(table par DuckDB, table par Python, journal de la voie DuckDB) d'un lecteur seul."""
    out = []
    for sql in (True, False):
        flow = Flow.model_validate({"nodes": [{"id": "r", "type": kind, "params": params}], "links": []})
        events = []
        run_dir = tmp_path / f"run_{sql}"
        res = run_flow(flow, registry, run_dir, sql_route=sql, on_event=events.append)
        assert res.status == "done", res.error
        logs = [e["message"] for e in events if e["type"] == "log"]
        out.append((pq.read_table(port_data(run_dir / "r" / "output.parquet")), logs))
    (duck, logs), (python, _) = out
    return duck, python, logs


def kind(field):
    """Type d'une colonne ; un texte encodé par dictionnaire est un texte (DuckDB le décode)."""
    return str(field.type.value_type if pa.types.is_dictionary(field.type) else field.type)


def same(duck, python):
    assert duck.schema.names == python.schema.names
    assert [kind(f) for f in duck.schema] == [kind(f) for f in python.schema]
    assert get_crs(duck) == get_crs(python)
    rest = [n for n in duck.schema.names if n != GEOM]
    assert duck.select(rest).to_pylist() == python.select(rest).to_pylist()
    if GEOM in duck.schema.names:
        as_wkt = [
            [None if g is None else shapely.from_wkb(g).wkt for g in t.column(GEOM).to_pylist()] for t in (duck, python)
        ]
        assert as_wkt[0] == as_wkt[1]


def by_duckdb(logs):
    return any("DuckDB" in m for m in logs)


@pytest.fixture
def typed(tmp_path):
    """GeoParquet aux types variés, géométrie sous un autre nom (métadonnées « geo »)."""
    n = 4
    t = pa.table(
        {
            "i": pa.array([1, None, 3, 4], pa.int32()),
            "u": pa.array([1, 2, 3, 4], pa.uint16()),
            "f": [0.5, None, 2.5, 3.5],
            "s": ["a", "é", None, "d"],
            "b": [True, False, None, True],
            "d": pa.array([datetime.date(2024, 1, i + 1) for i in range(n)], pa.date32()),
            "ts": pa.array([datetime.datetime(2024, 1, 1, i) for i in range(n)], pa.timestamp("us")),
            "dec": pa.array([decimal.Decimal("1.25")] * n, pa.decimal128(10, 2)),
            "lst": [[1, 2], [], None, [3]],
            "cat": pa.array(["x", "y", "x", "y"]).dictionary_encode(),
            "geom": pa.array([shapely.to_wkb(shapely.Point(i, i)) for i in range(n - 1)] + [None], pa.binary()),
        }
    )
    geo = '{"version": "1.1.0", "primary_column": "geom", "columns": {"geom": {"encoding": "WKB"}}}'
    t = t.replace_schema_metadata({"geo": geo})
    pq.write_table(t, tmp_path / "typed.parquet")
    return tmp_path / "typed.parquet"


def test_parquet_types_geometry_name_and_crs(registry, tmp_path, typed):
    duck, python, logs = read_both(registry, tmp_path, "GeoParquetReader", {"path": str(typed)})
    assert by_duckdb(logs) and get_crs(duck) == "OGC:CRS84"  # défaut GeoParquet : pas de SCR déclaré
    same(duck, python)


def test_parquet_files_pattern_filename_and_row_limit(registry, tmp_path):
    for k in range(3):
        t = pa.table({"k": [k] * 5, GEOM: pa.array([shapely.to_wkb(shapely.Point(k, j)) for j in range(5)])})
        pq.write_table(with_crs(t, "EPSG:2154"), tmp_path / f"part_{k}.parquet")
    params = {"path": str(tmp_path / "part_*.parquet"), "add_filename": True, "max_rows": 7}
    duck, python, logs = read_both(registry, tmp_path, "GeoParquetReader", params)
    assert by_duckdb(logs) and duck.num_rows == 7
    assert duck.column("filename").to_pylist() == ["part_0.parquet"] * 5 + ["part_1.parquet"] * 2
    same(duck, python)


def test_parquet_without_geometry(registry, tmp_path):
    pq.write_table(pa.table({"code": ["031", "009"], "pop": [10, 20]}), tmp_path / "plain.parquet")
    duck, python, logs = read_both(registry, tmp_path, "GeoParquetReader", {"path": str(tmp_path / "plain.parquet")})
    assert "fichier lu sur place, sans copie" in logs and get_crs(duck) is None  # déjà au format des ports (ADR 0043)
    same(duck, python)


CSV = "id;nom;x;y;wkt\n1;Aé;1,5;2;POINT (1 2)\n2;B;;3;POLYGON ((0 0, 1 0, 1 1, 0 0))\n3;C;4;5;pas du wkt\n4;D;6;7;\n"


@pytest.mark.parametrize(
    "params",
    [
        {"wkt_column": "wkt", "crs": "EPSG:2154"},
        {"x_column": "x", "y_column": "y", "crs": "EPSG:2154", "add_filename": True},
        {"detect_types": True, "max_rows": 2},
        {"delimiter": ";", "header": False},
    ],
)
def test_csv_geometry_types_and_options(registry, tmp_path, params):
    (tmp_path / "data.csv").write_bytes(CSV.encode("utf-8"))
    duck, python, logs = read_both(registry, tmp_path, "CsvReader", {"path": str(tmp_path / "data.csv"), **params})
    assert by_duckdb(logs)
    same(duck, python)


def test_csv_latin1(registry, tmp_path):
    (tmp_path / "latin.csv").write_bytes("id,nom\n1,Rémi\n2,Agnès\n".encode("latin-1"))
    duck, python, logs = read_both(registry, tmp_path, "CsvReader", {"path": str(tmp_path / "latin.csv")})
    assert by_duckdb(logs) and duck.column("nom").to_pylist() == ["Rémi", "Agnès"]
    same(duck, python)


def test_unreadable_coordinates_fall_back_to_the_python_reader(registry, tmp_path):
    """Une valeur que DuckDB refuse : la lecture repasse par Python, qui dit pourquoi (même message qu'avant)."""
    (tmp_path / "bad.csv").write_bytes(b"x,y\n1,2\nabc,3\n")
    flow = Flow.model_validate(
        {
            "nodes": [
                {
                    "id": "r",
                    "type": "CsvReader",
                    "params": {"path": str(tmp_path / "bad.csv"), "x_column": "x", "y_column": "y"},
                }
            ],
            "links": [],
        }
    )
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "error" and "abc" in (res.nodes["r"].error or "")
