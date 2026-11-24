"""ReorderAttributes : change l'ordre des attributs, sans en retirer."""

from __future__ import annotations

from pydantic import Field

from etl_engine.column_order import Others, reorder, reorder_schema
from etl_engine.expr import quote
from etl_engine.sdk import AttributeOrder, Modifier, Params


class ReorderAttributes(Modifier):
    name = "ReorderAttributes"
    label = "Réordonner des attributs"
    category = "Attributs"
    description = "Place des attributs dans l'ordre choisi ; les autres sont gardés, à la suite."
    help = (
        "Les attributs de « Ordre » viennent dans cet ordre ; les autres gardent leur ordre d'arrivée, après eux (ou "
        "avant, selon « Autres attributs »). Aucun attribut n'est retiré ; la géométrie reste en dernier. Un attribut "
        "de la liste absent en amont est une erreur. Les valeurs et l'ordre des entités ne changent pas."
    )
    rejected = False

    class Params(Params):
        attributes: AttributeOrder = Field(
            default_factory=list, title="Ordre", description="Attributs placés dans cet ordre (flèches pour déplacer)."
        )
        others: Others = Field(
            "after",
            title="Autres attributs",
            description="after : après ceux de la liste ; before : avant eux.",
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        return {
            "output": None if schema is None else reorder_schema(schema, self.params.attributes, self.params.others)
        }

    def sql(self, relations, schemas):
        schema = schemas.get("input")
        if schema is None:
            return None
        names = reorder_schema(schema, self.params.attributes, self.params.others).names
        return {"output": f"SELECT {', '.join(quote(n) for n in names)} FROM {relations['input']}"}

    def process(self, batch):
        return {"output": reorder(batch, self.params.attributes, self.params.others)}
