"""ReplaceNulls : remplace les valeurs vides, ou transforme des valeurs en vides."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params, missing_attributes


class ReplaceNulls(Modifier):
    name = "ReplaceNulls"
    label = "Valeurs vides"
    category = "Attributs"
    description = "Remplace les valeurs vides (NULL) par une valeur, ou transforme une valeur donnée en vide."
    help = (
        "« fill » : les vides deviennent la valeur choisie (convertie au type de l'attribut ; impossible = erreur "
        "de configuration). « to_null » : les valeurs égales à la valeur choisie (ex. « -1 », « N/A ») "
        "deviennent vides. "
        "« Compter le texte vide » traite aussi « » et les espaces comme des vides. Sans attribut : tous."
    )
    rejected = False

    class Params(Params):
        attributes: AttributeList = Field([], title="Attributs", description="Vide = tous (hors géométrie).")
        mode: Literal["fill", "to_null"] = Field(
            "fill",
            title="Action",
            description=(
                "fill : remplace les valeurs vides par « Valeur » ; to_null : vide les valeurs égales à « Valeur "
                "» (ex. -1, N/A)."
            ),
        )
        value: str = Field(
            "",
            title="Valeur",
            description="fill : valeur écrite à la place des vides ; to_null : valeur à considérer comme vide.",
        )
        blank_is_null: bool = Field(
            False,
            title="Compter le texte vide comme vide",
            description="Traite aussi le texte vide et les espaces comme des valeurs vides.",
        )

    def _columns(self, names: list[str]) -> list[str]:
        wanted = list(self.params.attributes) or [n for n in names if n != GEOM]
        missing = [a for a in wanted if a not in names]
        if missing:
            raise missing_attributes(missing)
        return [a for a in wanted if a != GEOM]

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is not None:
            self._columns(schema.names)
        return {"output": schema}

    def sql(self, relations, schemas):
        """Attributs texte, sans « vide = espaces » (Arrow et DuckDB ne retirent pas les mêmes espaces) : sinon la voie
        par lots."""
        p = self.params
        schema = schemas.get("input")
        if schema is None or p.blank_is_null:
            return None
        targets = self._columns(schema.names)
        if any(not pa.types.is_string(schema.field(n).type) for n in targets):
            return None
        value = "'" + p.value.replace("'", "''") + "'"

        def expr(name: str) -> str:
            col = quote(name)
            if p.mode == "fill":
                return f"coalesce({col}, {value})"
            return f"CASE WHEN {col} = {value} THEN NULL ELSE {col} END"

        columns = ", ".join(f"{expr(n)} AS {quote(n)}" if n in targets else quote(n) for n in schema.names)
        return {"output": f"SELECT {columns} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        out = batch
        for name in self._columns(batch.column_names):
            column = out.column(name)
            kind = column.type
            if p.blank_is_null and (pa.types.is_string(kind) or pa.types.is_large_string(kind)):
                blank = pc.equal(pc.utf8_trim_whitespace(column), "")
                column = pc.if_else(blank, pa.scalar(None, kind), column)
            if p.mode == "fill":
                try:
                    fill = pa.scalar(p.value).cast(kind) if not pa.types.is_string(kind) else pa.scalar(p.value, kind)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    raise ConfigError(f"« {p.value} » n'est pas une valeur valide pour « {name} » ({kind})") from None
                column = pc.fill_null(column, fill)
            else:
                text = pc.cast(column, pa.string())
                column = pc.if_else(pc.fill_null(pc.equal(text, p.value), False), pa.scalar(None, kind), column)
            out = out.set_column(out.column_names.index(name), name, column)
        return {"output": with_crs(out, get_crs(batch))}
