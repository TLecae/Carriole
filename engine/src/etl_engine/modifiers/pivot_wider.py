"""PivotWider : des lignes vers des colonnes (une colonne par valeur d'un attribut)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect, duck, quote
from etl_engine.sdk import (
    Attribute,
    AttributeList,
    ConfigError,
    Emit,
    Modifier,
    Params,
    missing_attributes,
    require_attributes,
)

Function = Literal["first", "sum", "count", "min", "max", "mean", "concat"]


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class PivotWider(Modifier):
    name = "PivotWider"
    label = "Pivoter (lignes en colonnes)"
    category = "Attributs"
    description = "Transforme les valeurs d'un attribut en colonnes : une ligne par groupe, une colonne par valeur."
    help = (
        "Les entités sont regroupées par « Regrouper par » ; chaque valeur de « Attribut à pivoter » devient une colonne "
        "(préfixe + valeur), remplie avec « Attribut des valeurs » combiné par la fonction choisie : first (la première), "
        "sum, mean, min, max, count (nombre de lignes) ou concat (valeurs réunies par une virgule). Pour que le schéma soit "
        "connu avant l'exécution (formulaires des nœuds suivants), lister les valeurs attendues dans « Colonnes » ; sinon "
        "elles sont découvertes dans les données, par ordre alphabétique. La géométrie n'est pas conservée. "
        "Exemple : (commune, annee, population) devient (commune, pop_2020, pop_2021, …)."
    )
    mode = "blocking"
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field(
            default_factory=list, title="Regrouper par", description="Attributs qui identifient une ligne du résultat."
        )
        pivot_attribute: Attribute = Field(
            "", title="Attribut à pivoter", description="Ses valeurs deviennent les noms des colonnes."
        )
        value_attribute: Attribute = Field(
            "", title="Attribut des valeurs", description="Valeur placée dans chaque colonne (inutile avec count)."
        )
        function: Function = Field(
            "first",
            title="Fonction",
            description="Comment combiner plusieurs lignes d'un même groupe et d'une même valeur.",
        )
        prefix: str = Field("", title="Préfixe des colonnes", description="Ajouté devant chaque valeur pivotée.")
        columns: list[str] = Field(
            default_factory=list,
            title="Colonnes",
            description="Valeurs attendues (donnent le schéma avant l'exécution) ; vide = découvertes dans les données.",
        )

    def _check(self) -> None:
        p = self.params
        if not p.pivot_attribute:
            raise ConfigError("attribut à pivoter non renseigné")
        if p.function != "count" and not p.value_attribute:
            raise ConfigError("attribut des valeurs non renseigné")
        if p.pivot_attribute in p.group_by:
            raise ConfigError("l'attribut à pivoter ne peut pas aussi servir à regrouper")

    def _aggregate(self, filter_sql: str) -> str:
        p = self.params
        value = quote(p.value_attribute) if p.value_attribute else "1"
        match p.function:
            case "first":
                expr = f"ARG_MIN({value}, __row)"  # la première dans l'ordre d'arrivée, quel que soit le parallélisme
            case "count":
                expr = "COUNT(*)"
            case "concat":
                expr = f"STRING_AGG(CAST({value} AS VARCHAR), ', ' ORDER BY __row)"
            case "mean":
                expr = f"AVG(TRY_CAST({value} AS DOUBLE))"
            case "sum":
                expr = f"SUM(TRY_CAST({value} AS DOUBLE))"
            case fn:
                expr = f"{fn.upper()}({value})"
        return f"{expr} FILTER (WHERE {filter_sql})"

    def _pivot(self, table: pa.Table, values: list[str]) -> pa.Table:
        p = self.params
        pivot = f"CAST({quote(p.pivot_attribute)} AS VARCHAR)"
        selects = [quote(g) for g in p.group_by]
        for v in values:
            selects.append(f"{self._aggregate(f'{pivot} = {_literal(v)}')} AS {quote(p.prefix + v)}")
        sql = f"SELECT {', '.join(selects)} FROM t" + (
            f" GROUP BY {', '.join(quote(g) for g in p.group_by)}" if p.group_by else ""
        )
        if p.group_by:
            sql += " ORDER BY " + ", ".join(quote(g) for g in p.group_by)
        return duck(sql, t=table.append_column("__row", pa.array(range(table.num_rows), pa.int64())))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        schema = inputs.get("input")
        require_attributes(schema, [*self.params.group_by, self.params.pivot_attribute, self.params.value_attribute])
        if schema is None or not self.params.columns:
            return {"output": None}
        empty = schema.empty_table()
        return {"output": self._pivot(empty, self.params.columns).schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        missing = [a for a in [*p.group_by, p.pivot_attribute, p.value_attribute] if a and a not in table.column_names]
        if missing:
            raise missing_attributes(missing)
        values = list(p.columns)
        if not values:
            found = duck(
                f"SELECT DISTINCT CAST({quote(p.pivot_attribute)} AS VARCHAR) AS v FROM t WHERE {quote(p.pivot_attribute)} IS NOT NULL ORDER BY v",
                t=table,
            )
            values = found.column("v").to_pylist()
        out = self._pivot(table, values)
        self.ctx.info(f"{table.num_rows} entités pivotées en {out.num_rows} lignes et {len(values)} colonnes")
        emit("output", out)
