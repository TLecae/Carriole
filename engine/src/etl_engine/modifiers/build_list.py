"""BuildList : regroupe les valeurs d'un attribut en une liste, une entité par groupe."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect, duck, quote
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import Attribute, AttributeList, ConfigError, Emit, Modifier, Params


class BuildList(Modifier):
    name = "BuildList"
    label = "Construire une liste"
    category = "Attributs"
    description = (
        "Regroupe les valeurs d'un attribut dans une liste : une entité par groupe, avec la liste des valeurs."
    )
    help = (
        "Les entités sont regroupées par « Regrouper par » (vide = un seul groupe) ; la liste réunit les valeurs de "
        "« Attribut des valeurs », dans l'ordre d'arrivée (ou triées, ou sans doublon). Les valeurs vides sont ignorées. "
        "Géométrie : « none » (aucune) ou « first » (celle de la première entité du groupe). La liste est un attribut "
        "de type liste : « Éclater une liste » fait l'inverse."
    )
    mode = "blocking"
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field(
            default_factory=list, title="Regrouper par", description="Vide = un seul groupe pour toutes les entités."
        )
        attribute: Attribute = Field(
            "", title="Attribut des valeurs", description="Les valeurs qui vont dans la liste."
        )
        list_name: str = Field(
            "", title="Attribut liste", description="Nom de la liste créée (vide = `attribut` + _liste)."
        )
        distinct: bool = Field(False, title="Sans doublon", description="Une seule fois chaque valeur.")
        sort: bool = Field(False, title="Trier", description="Valeurs triées ; sinon dans l'ordre d'arrivée.")
        geometry: Literal["none", "first"] = Field(
            "none", title="Géométrie", description="none : aucune ; first : celle de la première entité du groupe."
        )

    def _name(self) -> str:
        if not self.params.attribute:
            raise ConfigError("attribut des valeurs non renseigné")
        return self.params.list_name or f"{self.params.attribute}_liste"

    def _query(self, names: list[str]) -> str:
        p = self.params
        value = quote(p.attribute)
        if p.distinct and p.sort:
            agg = f"LIST(DISTINCT {value} ORDER BY {value})"
        elif p.distinct:
            agg = f"LIST(DISTINCT {value})"
        elif p.sort:
            agg = f"LIST({value} ORDER BY {value})"
        else:
            agg = f"LIST({value} ORDER BY __row)"
        selects = [quote(g) for g in p.group_by]
        selects.append(f"{agg} FILTER (WHERE {value} IS NOT NULL) AS {quote(self._name())}")
        if p.geometry == "first" and GEOM in names:
            selects.append(f"ARG_MIN({quote(GEOM)}, __row) AS {quote(GEOM)}")
        sql = f"SELECT {', '.join(selects)} FROM t"
        if p.group_by:
            sql += " GROUP BY " + ", ".join(quote(g) for g in p.group_by) + " ORDER BY MIN(__row)"
        return sql

    def _compute(self, table: pa.Table) -> pa.Table:
        missing = [a for a in [*self.params.group_by, self.params.attribute] if a not in table.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")
        out = duck(
            self._query(table.column_names), t=table.append_column("__row", pa.array(range(table.num_rows), pa.int64()))
        )
        return with_crs(out, get_crs(table))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._name()
        schema = inputs.get("input")
        return {"output": None if schema is None else self._compute(schema.empty_table()).schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._name()
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        out = self._compute(table)
        self.ctx.info(f"{table.num_rows} entités regroupées en {out.num_rows}")
        emit("output", out)
