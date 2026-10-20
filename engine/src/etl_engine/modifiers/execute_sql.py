"""ExecuteSql : une requête SQL DuckDB sur une ou plusieurs entrées du flux."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.querier import IDENT, check_select, run_query
from etl_engine.sdk import ConfigError, Emit, Modifier, Params, SqlCode


class ExecuteSql(Modifier):
    name = "ExecuteSql"
    label = "Requête SQL sur les entrées"
    category = "Calculs"
    description = (
        "Exécute une requête SQL DuckDB sur une ou plusieurs entrées du flux (jointures, agrégats, fenêtres, spatial)."
    )
    help = (
        "Chaque entrée est une table dont le nom est celui du port : `SELECT p.nom, COUNT(*) AS n FROM input p GROUP BY "
        "p.nom`. Plusieurs entrées (liste « Entrées ») permettent des jointures : `SELECT a.*, b.valeur FROM a JOIN b ON "
        "a.code = b.code`. La géométrie est la colonne `geometry` (fonctions ST_… de DuckDB : ST_Area, ST_Intersects, "
        "ST_Buffer…) et revient dans le résultat avec le système de coordonnées de la première entrée. Une seule "
        "instruction SELECT ; ni lecture de fichier ni accès réseau. Le résultat sort sur « output »."
    )
    rejected = False
    mode = "blocking"

    class Params(Params):
        inputs: list[str] = Field(
            default_factory=lambda: ["input"],
            title="Entrées",
            description="Noms des ports d'entrée (lettres, chiffres et _) ; chacun est une table de la requête.",
            json_schema_extra={"default": ["input"]},
        )
        sql: SqlCode = Field(
            "SELECT * FROM input",
            title="Requête SQL",
            description="Une instruction SELECT (ou WITH … SELECT) DuckDB.",
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        names = [n for n in getattr(params, "inputs", None) or [] if isinstance(n, str) and n]
        return (tuple(dict.fromkeys(names)) or cls.inputs), cls.outputs

    def _names(self) -> list[str]:
        names = self.params.inputs
        if not names:
            raise ConfigError("au moins une entrée est nécessaire")
        for n in names:
            if not IDENT.match(n):
                raise ConfigError(f"nom d'entrée invalide : « {n} » (lettres, chiffres et _)")
        if len(set(names)) != len(names):
            raise ConfigError("entrée en double")
        return list(names)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        names = self._names()
        check_select(self.params.sql)
        schemas = [inputs.get(n) for n in names]
        if any(s is None for s in schemas):
            return {"output": None}
        empty = {n: s.empty_table() for n, s in zip(names, schemas, strict=True) if s is not None}
        return {"output": run_query(self.params.sql, empty).schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        names = self._names()
        tables: dict[str, pa.Table] = {}
        for name in names:
            table = collect(inputs.get(name, ()))
            if table is None:
                continue
            tables[name] = table
        missing = [n for n in names if n not in tables]
        if missing:
            self.ctx.warning(f"entrée sans données : {', '.join(missing)} ; rien n'est produit")
            return
        out = run_query(self.params.sql, tables)
        self.ctx.info(f"{out.num_rows} ligne(s) renvoyée(s)")
        if out.num_rows:
            emit("output", out)
