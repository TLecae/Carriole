"""Oracle : dialecte de `dbapi` (pilote `oracledb` en mode « thin » : aucun client Oracle à installer).

Géométrie : `SDO_GEOMETRY` (Oracle Spatial / Locator), lue par `SDO_UTIL.TO_WKBGEOMETRY`, SRID par `.SDO_SRID` ;
écrite par le constructeur `SDO_GEOMETRY(wkb, srid)`. La connexion désigne un **service** (`hôte:port/service`).

Noms : Oracle range en majuscules les noms saisis sans guillemets ; une table ou une colonne créée par Carriole l'est
aussi (`communes` devient `COMMUNES`), comme le fait GDAL. Les ordres DDL (CREATE, DROP) sont validés par Oracle
dès leur exécution : en mode « overwrite », la table est recréée même si l'écriture échoue ensuite.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Sequence
from typing import Any

import oracledb
import pyarrow as pa
import pyproj

from etl_engine.connections import ConnectionInfo
from etl_engine.formats.dbapi import Column, Database, Dialect

oracledb.defaults.fetch_lobs = False  # BLOB / CLOB lus directement en octets / texte
_PLAIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
_AS_TEXT = {
    oracledb.DB_TYPE_ROWID,
    oracledb.DB_TYPE_UROWID,
    oracledb.DB_TYPE_INTERVAL_DS,
    oracledb.DB_TYPE_INTERVAL_YM,
    oracledb.DB_TYPE_LONG,
}
_ARROW = {
    oracledb.DB_TYPE_NUMBER: pa.float64(),
    oracledb.DB_TYPE_BINARY_DOUBLE: pa.float64(),
    oracledb.DB_TYPE_BINARY_FLOAT: pa.float32(),
    oracledb.DB_TYPE_BINARY_INTEGER: pa.int64(),
    oracledb.DB_TYPE_DATE: pa.timestamp("us"),
    oracledb.DB_TYPE_TIMESTAMP: pa.timestamp("us"),
    oracledb.DB_TYPE_TIMESTAMP_TZ: pa.timestamp("us", tz="UTC"),
    oracledb.DB_TYPE_TIMESTAMP_LTZ: pa.timestamp("us", tz="UTC"),
    oracledb.DB_TYPE_RAW: pa.binary(),
    oracledb.DB_TYPE_BLOB: pa.binary(),
    oracledb.DB_TYPE_BOOLEAN: pa.bool_(),
}


class Oracle(Dialect):
    kind = "oracle"
    label = "Oracle"
    spatial = "Oracle Spatial"
    default_schema_sql = "SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM dual"
    null_geometry_literal = True  # SDO_GEOMETRY(NULL, srid) n'est pas permis : la géométrie absente est écrite NULL

    def connect(self, info: ConnectionInfo, read_only: bool) -> Any:
        return oracledb.connect(
            user=info.user,
            password=info.password,
            dsn=f"{info.host}:{info.port}/{info.dbname}",
            tcp_connect_timeout=15,
        )

    def errors(self) -> tuple[type[BaseException], ...]:
        return (oracledb.Error,)

    def server_info(self, conn: Any) -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM all_objects WHERE owner = 'MDSYS' AND object_name = 'SDO_GEOMETRY'")
            spatial = bool(cur.fetchone()[0])
        return {"server": f"Oracle {conn.version}", "spatial": "Oracle Spatial" if spatial else None}

    # -- noms
    def quote(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def normalize(self, name: str) -> str:
        return name.upper() if _PLAIN_NAME.match(name) else name

    def subquery(self, inner: str) -> str:
        return f"({inner}) q_"

    # -- lecture
    def describe(self, conn: Any, inner: str) -> list[Column]:
        with conn.cursor() as cur:
            cur.execute(self.empty(inner))
            description = cur.description
        out = []
        for d in description:
            if d.type_code is oracledb.DB_TYPE_OBJECT:
                is_geometry = getattr(d.type, "name", "") == "SDO_GEOMETRY"
                out.append(Column(d.name, "geometry" if is_geometry else "skip"))
            elif d.type_code in _AS_TEXT:
                out.append(Column(d.name, "text"))
            elif d.type_code is oracledb.DB_TYPE_XMLTYPE:
                out.append(Column(d.name, "skip"))
            else:
                out.append(Column(d.name, "value"))
        return out

    def geometry_select(self, column: str) -> str:
        return f"SDO_UTIL.TO_WKBGEOMETRY({column})"

    def geometry_text(self, column: str) -> str:
        return f"SDO_UTIL.TO_WKTGEOMETRY({column})"

    def text_select(self, column: str) -> str:
        return f"TO_CHAR({column})"

    def srid_sql(self, inner: str, column: str) -> str:
        q = self.quote(column)
        return f"SELECT q_.{q}.SDO_SRID FROM ({inner}) q_ WHERE q_.{q} IS NOT NULL FETCH FIRST 1 ROWS ONLY"

    def fetch_arrow(self, conn: Any, statement: str, batch_rows: int) -> Iterator[pa.Table]:
        empty = True
        # NUMBER(p, 0) en décimal exact, ramené en entier par Database._normalize (sinon : flottant)
        for frame in conn.fetch_df_batches(statement, size=batch_rows, fetch_decimals=True):
            empty = False
            yield pa.table(frame)
        if empty:  # aucun lot : un lot vide, typé d'après la description du résultat
            with conn.cursor() as cur:
                cur.execute(self.empty(statement))
                yield pa.schema([pa.field(d.name, self.arrow_type(d)) for d in cur.description]).empty_table()

    def arrow_type(self, description: Sequence[Any]) -> pa.DataType:
        code = description[1]
        if code is oracledb.DB_TYPE_NUMBER and description[5] == 0 and (description[4] or 0) and description[4] <= 19:
            return pa.int64()  # NUMBER(p, 0)
        return _ARROW.get(code, pa.string())

    def list_tables(self, conn: Any) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.owner, o.object_name, o.object_type, t.num_rows,
                          (SELECT MIN(c.column_name) FROM all_tab_columns c
                            WHERE c.owner = o.owner AND c.table_name = o.object_name
                              AND c.data_type = 'SDO_GEOMETRY')
                     FROM all_objects o
                     LEFT JOIN all_tables t ON t.owner = o.owner AND t.table_name = o.object_name
                    WHERE o.object_type IN ('TABLE', 'VIEW')
                      AND o.owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')
                      AND o.object_name NOT LIKE 'BIN$%' AND o.object_name NOT LIKE 'MDRT%'
                    ORDER BY 1, 2"""
            )
            rows = cur.fetchall()
        return [
            {
                "schema": r[0],
                "table": r[1],
                "kind": "v" if r[2] == "VIEW" else "r",
                "rows_estimate": int(r[3] or 0),
                "geometry": r[4],
            }
            for r in rows
        ]

    # -- écriture
    def exists(self, cur: Any, schema: str, table: str) -> bool:
        cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner = :1 AND table_name = :2", (schema, table))
        return bool(cur.fetchone()[0])

    def table_columns(self, cur: Any, schema: str, table: str) -> list[str]:
        cur.execute(
            "SELECT column_name FROM all_tab_columns WHERE owner = :1 AND table_name = :2 ORDER BY column_id",
            (schema, table),
        )
        return [str(r[0]) for r in cur.fetchall()]

    def column_type(self, t: pa.DataType, key: bool) -> str:
        if pa.types.is_boolean(t):
            return "NUMBER(1)"
        if pa.types.is_int8(t) or pa.types.is_int16(t) or pa.types.is_uint8(t):
            return "NUMBER(5)"
        if pa.types.is_int32(t) or pa.types.is_uint16(t):
            return "NUMBER(10)"
        if pa.types.is_uint64(t):
            return "NUMBER(20)"
        if pa.types.is_integer(t):
            return "NUMBER(19)"
        if pa.types.is_float16(t) or pa.types.is_float32(t):
            return "BINARY_FLOAT"
        if pa.types.is_floating(t):
            return "BINARY_DOUBLE"
        if pa.types.is_decimal(t):
            return f"NUMBER({min(t.precision, 38)}, {t.scale})"
        if pa.types.is_date(t):
            return "DATE"
        if pa.types.is_timestamp(t):
            return "TIMESTAMP WITH TIME ZONE" if t.tz else "TIMESTAMP"
        if pa.types.is_binary(t) or pa.types.is_large_binary(t):
            return "BLOB"
        if pa.types.is_dictionary(t):
            return self.column_type(t.value_type, key)
        return "VARCHAR2(1000 CHAR)" if key else "VARCHAR2(4000 CHAR)"

    def geometry_type(self) -> str:
        return "SDO_GEOMETRY"

    def param(self, i: int) -> str:
        return f":{i + 1}"

    def geometry_param(self, i: int, srid: int) -> str:
        return f"SDO_GEOMETRY(:{i + 1}, {int(srid) if srid else 'NULL'})"

    def truncate(self, target: str) -> str:
        return f"DELETE FROM {target}"  # TRUNCATE est un DDL, validé aussitôt : DELETE reste dans la transaction

    def prepare_insert(self, cur: Any, kinds: list[str]) -> None:
        if "geometry" in kinds:
            cur.setinputsizes(*[oracledb.DB_TYPE_BLOB if k == "geometry" else None for k in kinds])

    def merge(self, target: str, columns: list[str], key: list[str], values: list[str]) -> str:
        source = ", ".join(f"{v} AS {c}" for v, c in zip(values, columns, strict=True))
        on = " AND ".join(f"t.{k} = s.{k}" for k in key)
        others = [c for c in columns if c not in key]
        update = f"WHEN MATCHED THEN UPDATE SET {', '.join(f't.{c} = s.{c}' for c in others)} " if others else ""
        return (
            f"MERGE INTO {target} t USING (SELECT {source} FROM dual) s ON ({on}) {update}"
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}) VALUES ({', '.join(f's.{c}' for c in columns)})"
        )

    def after_create(self, cur, schema, table, geometry_column, srid, bounds, has_key) -> list[str]:
        cur.execute(self.default_schema_sql)
        if str(cur.fetchone()[0]) != schema:
            return ["index spatial non créé : table d'un autre schéma (métadonnées USER_SDO_GEOM_METADATA)"]
        geographic = False
        if srid:
            with contextlib.suppress(pyproj.exceptions.CRSError):
                geographic = pyproj.CRS.from_epsg(srid).is_geographic
        tolerance = 0.05 if geographic else 0.005
        xmin, ymin, xmax, ymax = bounds
        if geographic:
            xmin, ymin, xmax, ymax = -180, -90, 180, 90
        else:
            pad = max(xmax - xmin, ymax - ymin, 1) * 0.01
            xmin, ymin, xmax, ymax = xmin - pad, ymin - pad, xmax + pad, ymax + pad
        cur.execute(
            "DELETE FROM user_sdo_geom_metadata WHERE table_name = :1 AND column_name = :2", (table, geometry_column)
        )
        cur.execute(
            "INSERT INTO user_sdo_geom_metadata (table_name, column_name, diminfo, srid) VALUES (:1, :2, "
            "SDO_DIM_ARRAY(SDO_DIM_ELEMENT('X', :3, :4, :7), SDO_DIM_ELEMENT('Y', :5, :6, :7)), :8)",
            (table, geometry_column, xmin, xmax, ymin, ymax, tolerance, srid or None),
        )
        index = self.quote(f"SIDX_{table}"[:128])
        cur.execute(
            f"CREATE INDEX {index} ON {self.table_ref(schema, table)} ({self.quote(geometry_column)}) "
            "INDEXTYPE IS MDSYS.SPATIAL_INDEX_V2"
        )
        return ["index spatial créé"]

    def placeholder(self, name: str) -> str:
        return f":{name}"


database = Database(Oracle())
