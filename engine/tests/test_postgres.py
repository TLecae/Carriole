"""PostgreSQL / PostGIS : nécessite une base PostGIS de test (variable ETL_TEST_PG, fournie par la CI)."""

import datetime as dt
import os
import uuid

import numpy as np
import psycopg
import pyarrow as pa
import pytest
import shapely
from etl_engine import connections
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.formats import pg
from etl_engine.modifiers.postgres import PostgresReader, PostgresWriter
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier

DSN = os.environ.get("ETL_TEST_PG")
pytestmark = pytest.mark.skipif(not DSN, reason="ETL_TEST_PG non défini (base PostGIS de test)")


@pytest.fixture
def db(monkeypatch):
    """Schéma jetable + connexion nommée « test » ; renvoie (schéma, connexion psycopg autocommit)."""
    monkeypatch.setenv("ETL_CONN_TEST", DSN)
    schema = "t_" + uuid.uuid4().hex[:10]
    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    conn.execute(f'CREATE SCHEMA "{schema}"')
    yield schema, conn
    conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    conn.close()


def points(n: int = 10, crs: str | None = "EPSG:2154") -> pa.Table:
    pts = shapely.points(np.arange(n, dtype=float) + 700000, np.arange(n, dtype=float) + 6500000)
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array([f"é'{i}" for i in range(n)]),
            "val": pa.array([i / 2 for i in range(n)], pa.float64()),
            GEOM: pa.array(shapely.to_wkb(pts), pa.binary()),
        }
    )
    return with_crs(t, crs)


def polygons(n: int, multi: bool = False) -> pa.Table:
    geoms = [shapely.box(i, 0, i + 1, 1) for i in range(n)]
    if multi:
        geoms = [shapely.MultiPolygon([g]) for g in geoms]
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array(["x"] * n),
            "val": pa.array([0.0] * n),
            GEOM: pa.array(shapely.to_wkb(geoms), pa.binary()),
        }
    )
    return with_crs(t, "EPSG:2154")


def write(schema, table, data, **opts):
    w = pg.PgWriter("test", f"{schema}.{table}", **opts)
    half = max(data.num_rows // 2, 1)
    w.write(data.slice(0, half))
    if data.num_rows > half:
        w.write(data.slice(half))
    w.close()
    return w


def read(schema, table, batch_rows=4, **opts):
    return pa.concat_tables(pg.read_batches("test", batch_rows, table=f"{schema}.{table}", **opts))


def count(conn, schema, table):
    return conn.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]


def test_roundtrip_points_creates_table_srid_and_gist_index(db):
    schema, conn = db
    w = write(schema, "p", points(), mode="create")
    assert any("créée" in m for m in w.messages)
    got = read(schema, "p")
    assert got.num_rows == 10
    assert got.column("nom").to_pylist() == [f"é'{i}" for i in range(10)]  # apostrophe et accents
    assert get_crs(got) == "EPSG:2154"
    assert shapely.from_wkb(got.column(GEOM).to_pylist()[3]).equals(shapely.Point(700003, 6500003))
    row = conn.execute(
        "SELECT type, srid FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = 'p'", (schema,)
    ).fetchone()
    assert row == ("POINT", 2154)
    index = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = 'p'", (schema,))
    assert any("gist" in r[0] for r in index.fetchall())


def test_read_schema_matches_data_and_streams_in_batches(db):
    schema, _ = db
    write(schema, "p", points(10), mode="create")
    sch = pg.read_schema("test", table=f"{schema}.p")
    assert sch.names == ["id", "nom", "val", GEOM] and get_crs(sch) == "EPSG:2154"
    batches = list(pg.read_batches("test", 3, table=f"{schema}.p"))
    assert [b.num_rows for b in batches] == [3, 3, 3, 1]
    assert all(b.schema.equals(sch, check_metadata=False) for b in batches)


def test_types_mapping(db):
    schema, conn = db
    conn.execute(
        f"""CREATE TABLE "{schema}".t (a smallint, b integer, c bigint, d real, e double precision, f numeric(10,2),
              g boolean, h text, i date, j timestamp, k timestamptz, l jsonb, m uuid, n integer[], o text[],
              p bytea, q interval, r varchar(5))"""
    )
    conn.execute(
        f"""INSERT INTO "{schema}".t VALUES (1, 2, 3, 1.5, 2.5, 12.34, true, 'x', '2026-01-02',
              '2026-01-02 03:04:05', '2026-01-02 03:04:05+01', '{{"a": 1}}', '3f2c1b7a-0000-4000-8000-000000000001',
              '{{1,2}}', '{{a,b}}', '\\x0102', '1 day', 'abc'), (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
              NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"""
    )
    got = read(schema, "t")
    types = {f.name: f.type for f in got.schema}
    assert types["a"] == pa.int16() and types["b"] == pa.int32() and types["c"] == pa.int64()
    assert types["d"] == pa.float32() and types["e"] == pa.float64() and types["f"] == pa.float64()
    assert types["g"] == pa.bool_() and types["i"] == pa.date32()
    assert types["j"] == pa.timestamp("us") and types["k"] == pa.timestamp("us", tz="UTC")
    assert types["n"] == pa.list_(pa.int32()) and types["o"] == pa.list_(pa.string())
    assert types["p"] == pa.binary() and types["q"] == pa.string() and types["m"] == pa.string()
    data = got.to_pydict()
    first = {k: v[0] for k, v in data.items()}
    assert first["f"] == 12.34 and first["i"] == dt.date(2026, 1, 2)
    assert first["k"] == dt.datetime(2026, 1, 2, 2, 4, 5, tzinfo=dt.UTC)
    assert first["l"].replace(" ", "") == '{"a":1}' and first["m"].endswith("0001")
    assert first["n"] == [1, 2] and first["p"] == b"\x01\x02" and first["r"] == "abc"
    assert all(v[1] is None for v in data.values())


def test_write_types_roundtrip(db):
    schema, conn = db
    t = pa.table(
        {
            "i": pa.array([1, None], pa.int32()),
            "f": pa.array([1.5, float("nan")]),
            "b": pa.array([True, None]),
            "d": pa.array([dt.date(2026, 1, 2), None]),
            "ts": pa.array([dt.datetime(2026, 1, 2, 3, 4), None]),
            "tz": pa.array([dt.datetime(2026, 1, 2, tzinfo=dt.UTC), None], pa.timestamp("us", tz="UTC")),
            "l": pa.array([[1, 2], None], pa.list_(pa.int64())),
            "s": pa.array(["a\tb\n\\c", None]),
        }
    )
    write(schema, "w", t, mode="create")
    cols = dict(
        conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s", (schema,)
        ).fetchall()
    )
    assert cols["i"] == "integer" and cols["tz"] == "timestamp with time zone" and cols["l"] == "ARRAY"
    got = read(schema, "w")
    assert got.column("s").to_pylist() == ["a\tb\n\\c", None]  # tabulation, saut de ligne et antislash survivent
    assert got.column("l").to_pylist() == [[1, 2], None]
    assert got.column("tz").to_pylist()[0] == dt.datetime(2026, 1, 2, tzinfo=dt.UTC)


def test_modes(db):
    schema, conn = db
    write(schema, "m", points(4), mode="create")
    with pytest.raises(ConfigError, match="existe déjà"):
        write(schema, "m", points(4), mode="create")
    assert count(conn, schema, "m") == 4
    write(schema, "m", points(4), mode="append")
    assert count(conn, schema, "m") == 8
    write(schema, "m", points(3), mode="truncate")
    assert count(conn, schema, "m") == 3
    write(schema, "m", points(5), mode="overwrite")
    assert count(conn, schema, "m") == 5


def test_upsert_updates_inserts_and_dedupes(db):
    schema, conn = db
    write(schema, "u", points(4), mode="create", key=["id"])
    changed = points(6).set_column(1, "nom", pa.array([f"nouveau {i}" for i in range(6)]))
    last = changed.slice(2, 1).set_column(1, "nom", pa.array(["dernier"]))
    write(schema, "u", pa.concat_tables([changed, last]), mode="upsert", key=["id"])
    rows = dict(conn.execute(f'SELECT id, nom FROM "{schema}".u ORDER BY id').fetchall())
    assert len(rows) == 6
    assert rows[0] == "nouveau 0" and rows[5] == "nouveau 5"
    assert rows[2] == "dernier"  # le dernier doublon du lot l'emporte
    with pytest.raises(ConfigError, match="clé"):
        pg.PgWriter("test", f"{schema}.u", mode="upsert")


def test_failed_write_rolls_back_everything(db):
    schema, conn = db
    write(schema, "r", points(4), mode="create", geometry_type="Point")
    w = pg.PgWriter("test", f"{schema}.r", mode="truncate")
    w.write(points(2))  # la table est vidée dans la transaction…
    with pytest.raises(ConfigError, match="écriture PostgreSQL"):
        w.write(polygons(2))  # …mais ce lot est refusé : tout est annulé
    assert count(conn, schema, "r") == 4


def test_polygons_and_multipolygons_become_multipolygon_layer(db):
    schema, conn = db
    mixed = pa.concat_tables([polygons(3), polygons(2, multi=True)])
    write(schema, "poly", mixed, mode="create")
    kind = conn.execute(
        "SELECT type FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = 'poly'", (schema,)
    ).fetchone()[0]
    assert kind == "MULTIPOLYGON"
    types = {shapely.from_wkb(w).geom_type for w in read(schema, "poly").column(GEOM).to_pylist()}
    assert types == {"MultiPolygon"}


def test_append_to_existing_rejects_unknown_columns(db):
    schema, _ = db
    write(schema, "c", points(2), mode="create")
    extra = points(2).append_column("inconnue", pa.array([1, 2]))
    with pytest.raises(ConfigError, match="inconnue"):
        write(schema, "c", extra, mode="append")


def test_reader_where_query_and_geometry_choice(db):
    schema, _ = db
    write(schema, "q", points(10), mode="create")
    assert read(schema, "q", where="id >= 7").num_rows == 3
    query = f'SELECT id, ST_Buffer(geom, 1) AS g2, geom FROM "{schema}".q'
    default = pa.concat_tables(pg.read_batches("test", 5, query=query + " WHERE id < 2"))
    assert default.column_names == ["id", GEOM, "geom"]  # première colonne géométrique = geometry
    assert shapely.from_wkb(default.column(GEOM).to_pylist()[0]).geom_type == "Polygon"
    chosen = pa.concat_tables(pg.read_batches("test", 5, query=query, geometry_column="geom"))
    assert shapely.from_wkb(chosen.column(GEOM).to_pylist()[0]).geom_type == "Point"
    assert chosen.schema.field("g2").type == pa.string()  # les autres géométries sont lues en WKT
    assert get_crs(chosen) == "EPSG:2154"
    assert get_crs(read(schema, "q", crs="EPSG:3857")) == "EPSG:3857"


def test_reader_is_read_only_and_errors_are_clean(db):
    schema, conn = db
    write(schema, "ro", points(2), mode="create")
    with pytest.raises(ConfigError, match="invalide"):
        list(pg.read_batches("test", 5, query=f'SELECT 1; DROP TABLE "{schema}".ro'))
    assert count(conn, schema, "ro") == 2
    with pytest.raises(ConfigError, match="invalide"):
        list(pg.read_batches("test", 5, table=f"{schema}.absente"))
    with pytest.raises(ConfigError, match="lecture PostgreSQL"):
        list(pg.read_batches("test", 5, table=f"{schema}.ro", where="nope = 1"))
    with pytest.raises(ConfigError, match=r"schéma\.table"):
        pg.split_table("a.b.c")


def test_unknown_connection_and_bad_password_do_not_leak_secret(monkeypatch):
    with pytest.raises(ConfigError, match="inconnue"):
        connections.resolve("nexistepas")
    password = DSN.split("://", 1)[1].split("@")[0].split(":", 1)[1]
    monkeypatch.setenv("ETL_CONN_MAUVAIS", DSN.replace(password, "secretmauvais"))
    with pytest.raises(ConfigError) as err:
        pg.connect("mauvais")
    assert "secretmauvais" not in str(err.value)


def test_modifiers_roundtrip_and_schema(db):
    schema, _ = db
    run_modifier(PostgresWriter, {"connection": "test", "table": f"{schema}.tr", "mode": "create"}, points(7))
    out = run_modifier(PostgresReader, {"connection": "test", "table": f"{schema}.tr", "where": "id < 5"})
    assert out["output"].num_rows == 5
    inst = PostgresReader(PostgresReader.Params(connection="test", table=f"{schema}.tr"), None)
    assert inst.output_schema({})["output"].names == ["id", "nom", "val", GEOM]
    with pytest.raises(ConfigError, match="connexion"):
        run_modifier(PostgresReader, {"table": "a.b"})


def test_list_tables_and_check_connection(db):
    schema, _ = db
    write(schema, "l", points(2), mode="create")
    tables = {(t["schema"], t["table"]): t for t in pg.list_tables("test")}
    assert tables[(schema, "l")]["geometry"] == "geom"
    info = pg.check_connection("test")
    assert info["postgis"] and "PostgreSQL" in info["server"]


# -------------------------------------------------------------------------- DatabaseQuery / DatabaseQueryPerFeature


def test_bind_placeholders_never_pastes_values():
    sql, names = pg.bind_placeholders("SELECT * FROM t WHERE a = {{x}} AND b LIKE 'z%' AND c = {{ x }} AND d = {{y}}")
    assert names == ["x", "y"]
    assert sql == "SELECT * FROM t WHERE a = %(x)s AND b LIKE 'z%%' AND c = %(x)s AND d = %(y)s"


def test_sql_creator_reads_and_blocks_writes_by_default(db):
    from etl_engine.modifiers.database_query import DatabaseQuery

    schema, conn = db
    write(schema, "sc", points(5), mode="create")
    out = run_modifier(
        DatabaseQuery,
        {
            "connection": "test",
            "sql": f'SELECT id, nom, ST_AsBinary(geom) AS geometry FROM "{schema}".sc WHERE id < 3',
            "crs": "EPSG:2154",
        },
    )["output"]
    assert out.num_rows == 3 and get_crs(out) == "EPSG:2154"
    assert shapely.from_wkb(out.column(GEOM).to_pylist()[1]).x == 700001
    with pytest.raises(ConfigError, match="SQL"):
        run_modifier(DatabaseQuery, {"connection": "test", "sql": f'DELETE FROM "{schema}".sc'})
    assert count(conn, schema, "sc") == 5


def test_sql_creator_write_commits_and_returning_gives_rows(db):
    from etl_engine.modifiers.database_query import DatabaseQuery

    schema, conn = db
    write(schema, "sw", points(5), mode="create")
    out = run_modifier(
        DatabaseQuery,
        {"connection": "test", "allow_write": True, "sql": f'DELETE FROM "{schema}".sw WHERE id >= 3 RETURNING id'},
    )["output"]
    assert sorted(out.column("id").to_pylist()) == [3, 4] and count(conn, schema, "sw") == 3
    run_modifier(
        DatabaseQuery, {"connection": "test", "allow_write": True, "sql": f'CREATE INDEX ON "{schema}".sw (id)'}
    )


def test_sql_executor_binds_attributes_and_routes_no_result(db):
    from etl_engine.modifiers.database_query_per_feature import DatabaseQueryPerFeature
    from etl_engine.testing import table

    schema, _ = db
    write(schema, "se", points(5), mode="create")
    # une valeur « piégée » reste une donnée : elle ne peut pas modifier la requête
    wanted = table(code=[1, 3, 99, 2], piege=["x", "y", "'; DROP TABLE x; --", "z"])
    sql = f'SELECT nom, val FROM "{schema}".se WHERE id = {{{{code}}}} AND nom <> {{{{piege}}}}'
    out = run_modifier(DatabaseQueryPerFeature, {"connection": "test", "sql": sql, "prefix": "r_"}, wanted)
    assert out["output"].column("code").to_pylist() == [1, 3, 2]
    assert out["output"].column("r_nom").to_pylist() == ["é'1", "é'3", "é'2"]
    assert out["NoResult"].column("code").to_pylist() == [99]
    inst = DatabaseQueryPerFeature(DatabaseQueryPerFeature.Params(connection="test", sql=sql, prefix="r_"), None)
    assert inst.output_schema({"input": wanted.schema})["output"].names == ["code", "piege", "r_nom", "r_val"]
    assert inst.used_attributes() == ["code", "piege"]


def test_sql_executor_all_rows_and_write_is_atomic(db):
    from etl_engine.modifiers.database_query_per_feature import DatabaseQueryPerFeature
    from etl_engine.testing import table

    schema, conn = db
    write(schema, "sa", points(6), mode="create")
    out = run_modifier(
        DatabaseQueryPerFeature,
        {
            "connection": "test",
            "rows": "all",
            "sql": f'SELECT id AS voisin FROM "{schema}".sa WHERE id > {{{{n}}}} AND id < {{{{n}}}} + 3',
        },
        table(n=[0, 3]),
    )["output"]
    assert out.column("n").to_pylist() == [0, 0, 3, 3] and out.column("voisin").to_pylist() == [1, 2, 4, 5]
    with pytest.raises(ConfigError):
        run_modifier(
            DatabaseQueryPerFeature,
            {
                "connection": "test",
                "allow_write": True,
                "sql": f'UPDATE "{schema}".sa SET nom = {{{{n}}}}::int::text RETURNING id',
            },
            table(n=["12", "pas un nombre"]),
        )
    assert conn.execute(f"SELECT count(*) FROM \"{schema}\".sa WHERE nom = '12'").fetchone()[0] == 0  # tout annulé
