"""RetainAttributes : ne garde que certains attributs."""

from __future__ import annotations

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, Modifier, Params, missing_attributes


class RetainAttributes(Modifier):
    name = "RetainAttributes"
    label = "Garder des attributs"
    category = "Attributs"
    description = "Ne conserve que les attributs choisis, dans l'ordre indiqué."
    help = "La géométrie est conservée sauf si on la retire explicitement. Un attribut absent est une erreur."

    class Params(Params):
        attributes: AttributeList = Field([], title="Attributs à garder", description="Dans cet ordre.")
        keep_geometry: bool = Field(
            True, title="Garder la géométrie", description="Décocher pour retirer aussi la géométrie."
        )

    def _apply(self, table: pa.Table) -> pa.Table:
        wanted = [a for a in self.params.attributes if a != GEOM]
        missing = [a for a in wanted if a not in table.column_names]
        if missing:
            raise missing_attributes(missing)
        if self.params.keep_geometry and GEOM in table.column_names:
            wanted.append(GEOM)
        return table.select(wanted)

    def output_schema(self, inputs):
        schema = inputs.get("input")
        return {"output": self._apply(schema.empty_table()).schema if schema is not None else None}

    def sql(self, relations, schemas):
        schema = schemas.get("input")
        if schema is None:
            return None
        names = self._apply(schema.empty_table()).column_names
        return {"output": f"SELECT {', '.join(quote(n) for n in names)} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        return {"output": self._apply(batch)}
