"""CastAttributes : change le type de plusieurs attributs (texte, entier, décimal, booléen, date…)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.blocking import quote
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.expr import guarded, safe_evaluate
from etl_engine.sdk import REJECTED, Attribute, ConfigError, Modifier, Params, reject

SQL_TYPES = {
    "text": "VARCHAR",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}
ARROW_TYPES = {
    "text": pa.string(),
    "integer": pa.int64(),
    "float": pa.float64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "datetime": pa.timestamp("us"),
}
Kind = Literal["text", "integer", "float", "boolean", "date", "datetime"]


class Conversion(Params):
    attribute: Attribute = Field("", title="Attribut", description="Attribut à convertir.")
    type: Kind = Field("text", title="Nouveau type", description="text, integer, float, boolean, date ou datetime.")
    decimal_comma: bool = Field(
        False,
        title="Virgule décimale",
        description="Lit « 12,5 » comme 12.5 (texte vers nombre) : retire les espaces et remplace la virgule.",
    )


class CastAttributes(Modifier):
    name = "CastAttributes"
    label = "Convertir le type d'attributs"
    category = "Attributs"
    description = "Change le type de plusieurs attributs à la fois : texte, entier, décimal, booléen, date."
    help = (
        "Chaque ligne convertit un attribut en place. Une valeur impossible à convertir (« abc » en entier) envoie "
        "l'entité sur « Rejected » avec le motif, sauf si « Valeur non convertible » vaut null : elle devient alors vide. "
        "« Virgule décimale » lit les nombres à la française (« 1 234,5 »). Booléens reconnus : true/false, oui/non, 1/0. "
        "Une valeur vide reste vide."
    )

    class Params(Params):
        conversions: list[Conversion] = Field(
            default_factory=list, title="Conversions", description="Une ligne par attribut converti."
        )
        on_error: Literal["reject", "null"] = Field(
            "reject",
            title="Valeur non convertible",
            description="reject : l'entité part sur « Rejected » ; null : la valeur devient vide.",
        )

    def _specs(self) -> list[Conversion]:
        specs = self.params.conversions
        if not specs:
            raise ConfigError("au moins une conversion est nécessaire")
        if any(not c.attribute for c in specs):
            raise ConfigError("chaque conversion demande un attribut")
        if any(c.attribute == GEOM for c in specs):
            raise ConfigError("la géométrie ne se convertit pas")
        return specs

    def output_schema(self, inputs):
        specs = self._specs()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        out = schema
        for c in specs:
            if c.attribute not in out.names:
                raise ConfigError(f"attribut absent : {c.attribute}")
            field = pa.field(c.attribute, ARROW_TYPES[c.type])
            out = out.set(out.get_field_index(c.attribute), field)
        return {"output": out}

    @staticmethod
    def _expression(c: Conversion, source: pa.DataType) -> str:
        col = quote(c.attribute)
        if c.type == "boolean" and (pa.types.is_string(source) or pa.types.is_large_string(source)):
            low = f"LOWER(TRIM({col}))"
            return (
                f"CASE WHEN {low} IN ('true','t','vrai','oui','o','yes','y','1') THEN TRUE "
                f"WHEN {low} IN ('false','f','faux','non','n','no','0') THEN FALSE ELSE NULL END"
            )
        if (
            c.decimal_comma
            and c.type in ("integer", "float")
            and (pa.types.is_string(source) or pa.types.is_large_string(source))
        ):
            col = f"REPLACE(REPLACE(REPLACE(TRIM({col}), ' ', ''), CHR(160), ''), ',', '.')"
        return f"TRY_CAST({col} AS {SQL_TYPES[c.type]})"

    def sql(self, relations, schemas):
        schema = schemas.get("input")
        out = self.output_schema(dict(schemas)).get("output")
        if schema is None or out is None:
            return None
        query = f"SELECT * FROM {relations['input']}"
        current = schema
        for c in self._specs():
            converted = self._expression(c, current.field(c.attribute).type)
            if self.params.on_error == "reject":
                # au contrôle, une valeur non convertible échoue (« # » ne se convertit en aucun de ces types) : le nœud
                # passe par la voie par lots, qui rejette l'entité avec le motif
                col = quote(c.attribute)
                fails = f"CAST('#' || CAST({col} AS VARCHAR) AS {SQL_TYPES[c.type]})"
                converted = f"CASE WHEN ({converted}) IS NULL AND {col} IS NOT NULL THEN {fails} ELSE ({converted}) END"
            query = f"SELECT * REPLACE ({guarded(converted)} AS {quote(c.attribute)}) FROM ({query})"
            current = current.set(current.get_field_index(c.attribute), pa.field(c.attribute, ARROW_TYPES[c.type]))
        return {"output": f"SELECT {', '.join(quote(n) for n in out.names)} FROM ({query})"}

    def sql_checked(self) -> list[str]:
        return [c.attribute for c in self.params.conversions if c.attribute]

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        specs = self._specs()
        table = batch
        alive = np.arange(batch.num_rows)
        rejected: list[pa.Table] = []
        for c in specs:
            if c.attribute not in table.column_names:
                raise ConfigError(f"attribut absent : {c.attribute}")
            source = table.column(c.attribute)
            result, errors = safe_evaluate(table, {"_converted": self._expression(c, source.type)})
            column = result.column("_converted").cast(ARROW_TYPES[c.type])
            failed = pc.and_(pc.is_null(column), pc.is_valid(source)).to_numpy(zero_copy_only=False).astype(bool)
            if self.params.on_error == "reject" and failed.any():
                for i in np.flatnonzero(failed):
                    errors.setdefault(int(i), f"conversion impossible de « {c.attribute} » en {c.type}")
            if errors:
                local = sorted(errors)
                rejected.append(
                    reject(batch.take(pa.array(alive[local])), pa.array([errors[i] for i in local], pa.string()))
                )
                keep = np.ones(table.num_rows, dtype=bool)
                keep[local] = False
                table = table.filter(pa.array(keep))
                column = column.filter(pa.array(keep))
                alive = alive[keep]
            table = table.set_column(table.column_names.index(c.attribute), c.attribute, column)
        out = {"output": with_crs(table, get_crs(batch))}
        if rejected:
            out[REJECTED] = pa.concat_tables(rejected)
        return out
