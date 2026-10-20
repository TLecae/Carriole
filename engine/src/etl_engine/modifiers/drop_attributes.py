"""DropAttributes : retire des attributs."""

from __future__ import annotations

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params


class DropAttributes(Modifier):
    name = "DropAttributes"
    label = "Retirer des attributs"
    category = "Attributs"
    description = "Supprime les attributs choisis ; tous les autres sont conservés."
    help = "Un attribut absent est une erreur de configuration (sauf si « Ignorer les absents » est coché)."

    class Params(Params):
        attributes: AttributeList = Field(
            [], title="Attributs à retirer", description="Attributs à supprimer ; vide = rien n'est retiré."
        )
        remove_geometry: bool = Field(
            False, title="Retirer aussi la géométrie", description="Retire aussi la géométrie des entités."
        )
        ignore_missing: bool = Field(
            False,
            title="Ignorer les attributs absents",
            description="Décoché : un attribut absent des données est une erreur ; coché : il est ignoré.",
        )

    def _apply(self, table: pa.Table) -> pa.Table:
        drop = [a for a in self.params.attributes if a != GEOM]
        missing = [a for a in drop if a not in table.column_names]
        if missing and not self.params.ignore_missing:
            raise ConfigError(f"attributs absents : {missing}")
        if self.params.remove_geometry and GEOM in table.column_names:
            drop.append(GEOM)
        return table.drop_columns([a for a in drop if a in table.column_names])

    def output_schema(self, inputs):
        schema = inputs.get("input")
        return {"output": self._apply(schema.empty_table()).schema if schema is not None else None}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        return {"output": self._apply(batch)}
