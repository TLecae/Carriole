"""OrderBy : trie les entités."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import duckdb
from pydantic import Field

from etl_engine.blocking import collect, duck, quote
from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import Attribute, ConfigError, Emit, Modifier, Params, require_attributes


class SortKey(Params):
    attribute: Attribute = Field("", title="Attribut", description="Attribut sur lequel trier.")
    order: Literal["ascending", "descending"] = Field(
        "ascending", title="Ordre", description="ascending : croissant ; descending : décroissant."
    )
    kind: Literal["natural", "number", "text"] = Field(
        "natural", title="Comparer comme", description="natural : selon le type ; number : convertit en nombre ; text."
    )


class OrderBy(Modifier):
    name = "OrderBy"
    label = "Trier"
    category = "Calculs"
    description = "Trie les entités selon un ou plusieurs attributs (valeurs vides à la fin)."
    help = (
        "Le tri est stable : à clés égales, l'ordre d'arrivée est conservé. « number » convertit le texte en "
        "nombre (une valeur non convertible est une erreur) ; « text » compare les valeurs comme du texte."
    )
    mode = "blocking"
    rejected = False

    class Params(Params):
        keys: list[SortKey] = Field(
            [], title="Clés de tri", description="Le premier attribut prime, les suivants départagent les égalités."
        )

    def _check(self) -> list[SortKey]:
        keys = [k for k in self.params.keys if k.attribute]
        if not keys:
            raise ConfigError("au moins une clé de tri est nécessaire")
        return keys

    def output_schema(self, inputs):
        keys = self._check()
        require_attributes(inputs.get("input"), [k.attribute for k in keys])
        return {"output": inputs.get("input")}

    def sql(self, relations, schemas):
        keys = self._check()
        schema = schemas.get("input")
        if schema is None:
            return None
        order = []
        for key in keys:
            if key.attribute not in schema.names:
                raise ConfigError(f"attribut absent : {key.attribute}")
            column = quote(key.attribute)
            expr = {"natural": column, "number": f"CAST({column} AS DOUBLE)", "text": f"CAST({column} AS VARCHAR)"}[
                key.kind
            ]
            order.append(f"{expr} {'DESC' if key.order == 'descending' else 'ASC'} NULLS LAST")
        numbered = f"SELECT row_number() OVER () AS __rn, * FROM {relations['input']}"
        return {"output": f"SELECT * EXCLUDE (__rn) FROM ({numbered}) ORDER BY {', '.join(order)}, __rn"}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        # la requête de la voie SQL, sur la table en mémoire : mêmes conversions (texte d'un nombre, espaces, NaN)
        query = self.sql({"input": "t"}, {"input": table.schema})["output"]
        try:
            ordered = duck(query, t=table)
        except duckdb.Error as exc:
            raise ConfigError(f"tri impossible : {str(exc).splitlines()[0]}") from None
        emit("output", with_crs(ordered, get_crs(table)))
