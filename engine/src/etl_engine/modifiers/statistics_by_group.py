"""StatisticsByGroup : statistiques par groupe, sur les entités et/ou en résumé."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect, duck, quote
from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params

Function = Literal["count", "sum", "min", "max", "mean", "median", "stddev", "distinct", "mode"]

_NUMERIC = {"sum": "SUM", "mean": "AVG", "median": "MEDIAN", "stddev": "STDDEV_SAMP"}


def _aggregate(attr: str, fn: str) -> str:
    col = quote(attr)
    if fn in _NUMERIC:
        return f"{_NUMERIC[fn]}(TRY_CAST({col} AS DOUBLE))"
    return {
        "count": f"COUNT({col})",
        "min": f"MIN({col})",
        "max": f"MAX({col})",
        "distinct": f"COUNT(DISTINCT {col})",
        "mode": f"MODE({col})",
    }[fn]


class StatisticsByGroup(Modifier):
    name = "StatisticsByGroup"
    label = "Calculer des statistiques"
    category = "Calculs"
    description = "Calcule des statistiques par groupe : somme, moyenne, min, max, médiane, comptage…"
    help = (
        "Les entités sortent sur « output » (avec les statistiques de leur groupe si « Ajouter aux entités » est "
        "coché) et une ligne par groupe sort sur « summary ». Les colonnes s'appellent `attribut_fonction` "
        "(ex. pop_sum) ; `_count` est le nombre d'entités du groupe. Somme, moyenne, médiane et écart-type "
        "convertissent la valeur en nombre (non convertible = ignorée). Sans groupe, tout est un seul groupe."
    )
    mode = "blocking"
    outputs = ("output", "summary")
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field(
            [], title="Regrouper par", description="Vide = un seul groupe pour toutes les entités."
        )
        attributes: AttributeList = Field(
            [],
            title="Attributs à analyser",
            description=(
                "Attributs sur lesquels calculer les statistiques (les colonnes s'appellent attribut_fonction, "
                "ex. pop_sum)."
            ),
        )
        functions: list[Function] = Field(
            ["count", "sum", "mean", "min", "max"], title="Statistiques", description="Calculées pour chaque attribut."
        )
        count_rows: bool = Field(
            True, title="Compter les entités (_count)", description="Ajoute _count : le nombre d'entités du groupe."
        )
        attach: bool = Field(
            True, title="Ajouter aux entités", description="Recopie les statistiques sur chaque entité."
        )

    def _check(self) -> None:
        p = self.params
        if p.attributes and not p.functions:
            raise ConfigError("choisir au moins une statistique")
        if not p.attributes and not p.count_rows:
            raise ConfigError("rien à calculer : choisir des attributs ou le comptage")

    def _stat_columns(self) -> list[tuple[str, str]]:
        p = self.params
        cols = [("_count", "COUNT(*)")] if p.count_rows else []
        cols += [(f"{a}_{f}", _aggregate(a, f)) for a in p.attributes for f in dict.fromkeys(p.functions)]
        return cols

    def _check_columns(self, names: list[str]) -> list[tuple[str, str]]:
        p = self.params
        missing = [a for a in [*p.group_by, *p.attributes] if a not in names]
        if missing:
            raise ConfigError(f"attributs absents : {missing}")
        stats = self._stat_columns()
        clash = [n for n, _ in stats if n in names]
        if clash:
            raise ConfigError(f"les statistiques écraseraient des attributs existants : {clash}")
        return stats

    def _queries(self, relation: str, names: list[str]) -> dict[str, str]:
        """Requêtes SQL (relation d'entrée -> `output` et `summary`) : les deux voies d'exécution les partagent."""
        p = self.params
        stats = self._check_columns(names)
        select = ", ".join(f"{expr} AS {quote(name)}" for name, expr in stats)
        keys = ", ".join(quote(g) for g in p.group_by)
        summary = f"SELECT {keys + ', ' if keys else ''}{select} FROM {relation} {'GROUP BY ' + keys if keys else ''}"
        if not p.attach:
            return {"output": f"SELECT * FROM {relation}", "summary": summary}
        cols = ", ".join(f"s.{quote(n)}" for n, _ in stats)
        on = " AND ".join(f"t.{quote(g)} IS NOT DISTINCT FROM s.{quote(g)}" for g in p.group_by) or "TRUE"
        numbered = f"SELECT row_number() OVER () AS __rn, * FROM {relation}"
        output = (
            f"SELECT t.* EXCLUDE (__rn), {cols} FROM ({numbered}) t LEFT JOIN ({summary}) s ON {on} ORDER BY t.__rn"
        )
        return {"output": output, "summary": summary}

    def sql(self, relations, schemas):
        self._check()
        schema = schemas.get("input")
        if schema is None:
            return None
        return self._queries(relations["input"], schema.names)

    def _compute(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        queries = self._queries("t", table.column_names)
        output = with_crs(duck(queries["output"], t=table), get_crs(table))
        return output, with_crs(duck(queries["summary"], t=table), get_crs(table))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None, "summary": None}
        output, summary = self._compute(schema.empty_table())
        return {"output": output.schema, "summary": summary.schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        output, summary = self._compute(table)
        emit("output", output)
        emit("summary", summary)
        self.ctx.info(f"{summary.num_rows} groupe(s) sur {table.num_rows} entités")
