"""DatabaseQuery : exécute un ordre SQL sur PostgreSQL et envoie les lignes obtenues."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.formats import databases
from etl_engine.sdk import ConfigError, DbConnection, Emit, Modifier, Params


class DatabaseQuery(Modifier):
    name = "DatabaseQuery"
    reusable = "partial"  # relu à chaque run, repris dans un run « jusqu'au nœud » (Modifier.reusable)
    label = "SQL (créateur)"
    category = "Entrées / sorties"
    description = "Exécute un ordre SQL sur une base (PostgreSQL, Oracle) et envoie les lignes qu'il renvoie."
    help = (
        "Le SQL est celui de la base de la connexion. Par défaut il est exécuté en **lecture seule** (SELECT). "
        "Cocher « Autoriser l'écriture » pour un UPDATE, un CREATE TABLE… (validé à la fin, annulé en cas d'erreur) ; "
        "un RETURNING (PostgreSQL) renvoie des lignes. Pour renvoyer une géométrie, la produire en WKB sous le nom "
        "« geometry » : ST_AsBinary(geom) AS geometry (PostGIS), "
        'SDO_UTIL.TO_WKBGEOMETRY(geom) AS "geometry" (Oracle) ; son système de coordonnées se règle dans '
        "« Système de coordonnées »."
    )
    inputs = ()
    outputs = ("output",)
    rejected = False
    stateful = True  # peut écrire : on ne le rejoue pas en parallèle

    class Params(Params):
        connection: DbConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
        sql: str = Field(
            "", title="SQL", description="Un seul ordre.", examples=["SELECT id, nom FROM public.communes"]
        )
        allow_write: bool = Field(
            False,
            title="Autoriser l'écriture",
            description="Décoché : lecture seule (SELECT). Coché : UPDATE, CREATE TABLE… autorisés, validés à la fin.",
        )
        crs: str = Field("", title="Système de coordonnées", description="Si le résultat contient « geometry ».")

    def _check(self) -> None:
        if not self.params.connection:
            raise ConfigError("connexion non renseignée")
        if not self.params.sql.strip():
            raise ConfigError("SQL vide")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        if self.params.allow_write:
            return {"output": None}  # un ordre d'écriture ne se décrit pas sans l'exécuter
        try:
            db = databases.backend(self.params.connection)
            return {
                "output": db.read_schema(self.params.connection, query=self.params.sql, crs=self.params.crs or None)
            }
        except ConfigError as exc:
            if "invalide" in str(exc):
                raise
            return {"output": None}
        except Exception:  # noqa: BLE001
            return {"output": None}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        table = databases.backend(p.connection).run_statement(p.connection, p.sql, allow_write=p.allow_write)
        if table is None:
            self.ctx.info("ordre SQL exécuté (aucune ligne renvoyée)")
            return
        if GEOM in table.column_names and not pa.types.is_binary(table.schema.field(GEOM).type):
            raise ConfigError(
                "la colonne « geometry » doit être en WKB (binaire) : ST_AsBinary(geom) (PostGIS), SDO_UTIL.TO_WKBGEOMETRY(geom) (Oracle)"
            )
        if p.crs:
            from etl_engine.data import with_crs

            table = with_crs(table, p.crs)
        emit("output", table)
        self.ctx.info(f"{table.num_rows} ligne(s) renvoyée(s)")
