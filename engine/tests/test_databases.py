"""Oracle : dialecte (sans serveur) et essais réels.

Les essais réels demandent une base de test : `ETL_TEST_ORACLE` (`oracle://utilisateur:…@hôte:1521/service`). Sans
Oracle Spatial (image « slim »), les essais géométriques sont sautés.
"""

import contextlib
import datetime as dt
import os
import uuid

import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine import connections
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.formats import databases
from etl_engine.formats.dbapi import Database
from etl_engine.formats.oracle import Oracle
from etl_engine.modifiers.database_io import OracleReader, OracleWriter
from etl_engine.modifiers.database_query_per_feature import DatabaseQueryPerFeature
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier

ORACLE = os.environ.get("ETL_TEST_ORACLE")


def points(n: int = 10, crs: str | None = "EPSG:2154", missing: int | None = None) -> pa.Table:
    pts = list(shapely.points(np.arange(n, dtype=float) + 700000, np.arange(n, dtype=float) + 6500000))
    wkb = [None if i == missing else shapely.to_wkb(p) for i, p in enumerate(pts)]
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array([f"é'{i}" if i % 3 else None for i in range(n)]),
            "val": pa.array([i / 2 for i in range(n)], pa.float64()),
            "jour": pa.array([dt.date(2026, 1, 1 + i % 28) for i in range(n)], pa.date32()),
            GEOM: pa.array(wkb, pa.binary()),
        }
    )
    return with_crs(t, crs)


# ------------------------------------------------------------------------------------- sans serveur


def test_connection_uris_give_the_database_type_and_its_options():
    ora = connections.from_uri("a", "oracle://sys:p%40ss@ora:1522/ORCLPDB1")
    assert (ora.kind, ora.host, ora.port, ora.dbname, ora.user, ora.password) == (
        "oracle",
        "ora",
        1522,
        "ORCLPDB1",
        "sys",
        "p@ss",
    )
    assert connections.from_uri("b", "oracle://u:p@ora/ORCLPDB1").port == 1521  # port par défaut du type
    pg = connections.from_uri("c", "postgresql://u:p@pg:5433/base")
    assert (pg.kind, pg.port, pg.dbname) == ("postgres", 5433, "base")
    for scheme in ("mysql", "mssql"):  # SQL Server n'est plus pris en charge
        with pytest.raises(ConfigError, match="type de base inconnu"):
            connections.from_uri("d", f"{scheme}://u@h/b")


def test_the_connection_type_picks_the_database(monkeypatch):
    monkeypatch.setenv("ETL_CONN_ORA", "oracle://u:p@h/s")
    monkeypatch.setenv("ETL_CONN_PG", "postgresql://u:p@h/b")
    assert databases.backend("ORA").d.label == "Oracle"
    assert databases.backend("PG") is databases.POSTGRES
    # un lecteur Oracle refuse une connexion PostgreSQL, avant même de se connecter
    with pytest.raises(ConfigError, match="pas du bon type"):
        run_modifier(OracleReader, {"connection": "PG", "table": "t"})


def test_attribute_values_are_bound_never_pasted():
    sql, names = Database(Oracle()).bind_placeholders(
        "SELECT * FROM t WHERE a = {{x}} AND b LIKE 'z%' AND c = {{ x }} AND d = {{y}}"
    )
    assert names == ["x", "y"] and sql == "SELECT * FROM t WHERE a = :x AND b LIKE 'z%' AND c = :x AND d = :y"


def test_oracle_names_follow_oracle_rules():
    d = Oracle()
    assert d.normalize("communes") == "COMMUNES" and d.normalize("Mixed Case") == "Mixed Case"
    assert d.quote('a"b') == '"a""b"'
    assert (
        d.column_type(pa.int64(), False) == "NUMBER(19)" and d.column_type(pa.string(), True) == "VARCHAR2(1000 CHAR)"
    )
    merge = d.merge('"T"', ['"ID"', '"NOM"'], ['"ID"'], [":1", ":2"])
    assert 'USING (SELECT :1 AS "ID", :2 AS "NOM" FROM dual)' in merge and 'UPDATE SET t."NOM" = s."NOM"' in merge


def test_decimals_become_integers_when_they_fit():
    t = pa.table({"a": pa.array([1, 2], pa.decimal128(20, 0)), "b": pa.array([1, 2], pa.decimal128(10, 2))})
    out = Database._normalize(t, None, False)
    assert out.schema.field("a").type == pa.int64() and out.schema.field("b").type == pa.float64()
    huge = pa.table({"a": pa.array([10**19], pa.decimal128(20, 0))})
    assert Database._normalize(huge, None, False).schema.field("a").type == pa.float64()  # au-delà de 64 bits


# ------------------------------------------------------------------------------------------ Oracle


@pytest.fixture
def oracle(monkeypatch):
    if not ORACLE:
        pytest.skip("ETL_TEST_ORACLE non défini (base Oracle de test)")
    monkeypatch.setenv("ETL_CONN_ORA", ORACLE)
    name = "T_" + uuid.uuid4().hex[:10].upper()
    yield name
    db = databases.backend("ORA")
    session = db.session("ORA", read_only=False)
    with contextlib.suppress(ConfigError):  # table jamais créée
        session.execute(f'DROP TABLE "{name}" PURGE', {})
    session.close()


def spatial() -> bool:
    return bool(databases.backend("ORA").check_connection("ORA")["spatial"])


def test_oracle_attributes_roundtrip_modes_and_read_only_sql(oracle):
    table = points(9).drop_columns([GEOM])
    run_modifier(OracleWriter, {"connection": "ORA", "table": oracle.lower(), "mode": "create", "key": ["id"]}, table)
    out = run_modifier(OracleReader, {"connection": "ORA", "table": oracle.lower(), "where": '"ID" < 5'})["output"]
    out = out.sort_by("ID")
    assert out.column_names == ["ID", "NOM", "VAL", "JOUR"] and out.schema.field("ID").type == pa.int64()
    assert out.column("NOM").to_pylist() == table.column("nom").to_pylist()[:5]
    changed = table.slice(0, 3).set_column(1, "nom", pa.array(["neuf"] * 3))
    run_modifier(OracleWriter, {"connection": "ORA", "table": oracle, "mode": "upsert", "key": ["id"]}, changed)
    db = databases.backend("ORA")
    assert db.run_statement("ORA", f"SELECT COUNT(*) AS n FROM \"{oracle}\" WHERE nom = 'neuf'").to_pylist() == [
        {"N": 3}
    ]
    db.run_statement("ORA", f'DELETE FROM "{oracle}"')  # sans « écriture » : annulé
    assert db.run_statement("ORA", f'SELECT COUNT(*) AS n FROM "{oracle}"').to_pylist() == [{"N": 9}]
    entities = pa.table({"k": [1, 42]})
    out = run_modifier(
        DatabaseQueryPerFeature,
        {"connection": "ORA", "sql": f'SELECT val FROM "{oracle}" WHERE id = {{{{k}}}}'},
        entities,
    )
    assert out["output"].column("VAL").to_pylist() == [0.5] and out["NoResult"].column("k").to_pylist() == [42]


def test_oracle_geometry_roundtrip(oracle):
    if not spatial():
        pytest.skip("base Oracle sans Oracle Spatial")
    run_modifier(
        OracleWriter, {"connection": "ORA", "table": oracle, "mode": "create", "key": ["id"]}, points(6, missing=2)
    )
    out = run_modifier(OracleReader, {"connection": "ORA", "table": oracle})["output"].sort_by("ID")
    assert get_crs(out) == "EPSG:2154" and out.column(GEOM).to_pylist()[2] is None
    assert shapely.from_wkb(out.column(GEOM).to_pylist()[1]).equals(shapely.Point(700001, 6500001))
