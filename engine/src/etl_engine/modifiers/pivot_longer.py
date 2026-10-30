"""PivotLonger : des colonnes vers des lignes."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params, missing_attributes


def _common_type(types: list[pa.DataType]) -> pa.DataType:
    first = types[0]
    return first if all(t == first for t in types) else pa.string()


class PivotLonger(Modifier):
    name = "PivotLonger"
    label = "Dépivoter (colonnes en lignes)"
    category = "Attributs"
    description = "Transforme des colonnes en lignes : une entité par colonne choisie, avec son nom et sa valeur."
    help = (
        "Chaque entité reçue donne une entité par attribut de « Attributs à dépivoter » : les autres attributs (et la "
        "géométrie) sont recopiés, « Attribut du nom » reçoit le nom de la colonne d'origine et « Attribut de la valeur » sa "
        "valeur. Si les colonnes n'ont pas le même type, les valeurs deviennent du texte. Les valeurs vides sont ignorées "
        "sauf si « Garder les valeurs vides » est coché. Exemple : (commune, pop_2020, pop_2021) devient "
        "(commune, annee, population)."
    )
    rejected = False

    class Params(Params):
        attributes: AttributeList = Field(
            default_factory=list, title="Attributs à dépivoter", description="Les colonnes qui deviennent des lignes."
        )
        name_attribute: str = Field(
            "attribute", title="Attribut du nom", description="Reçoit le nom de la colonne d'origine."
        )
        value_attribute: str = Field(
            "value", title="Attribut de la valeur", description="Reçoit la valeur de la colonne."
        )
        keep_nulls: bool = Field(
            False, title="Garder les valeurs vides", description="Décoché : une valeur vide ne donne pas d'entité."
        )

    def _check(self) -> None:
        p = self.params
        if not p.attributes:
            raise ConfigError("au moins un attribut à dépivoter est nécessaire")
        if not p.name_attribute or not p.value_attribute:
            raise ConfigError("les attributs du nom et de la valeur demandent un nom")
        if p.name_attribute == p.value_attribute:
            raise ConfigError("l'attribut du nom et celui de la valeur doivent différer")
        if GEOM in p.attributes:
            raise ConfigError("la géométrie ne se dépivote pas")

    def _schema(self, schema: pa.Schema) -> pa.Schema:
        p = self.params
        missing = [a for a in p.attributes if a not in schema.names]
        if missing:
            raise missing_attributes(missing)
        kept = [f for f in schema if f.name not in p.attributes and f.name not in (p.name_attribute, p.value_attribute)]
        value_type = _common_type([schema.field(a).type for a in p.attributes])
        return pa.schema(
            [*kept, pa.field(p.name_attribute, pa.string()), pa.field(p.value_attribute, value_type)],
            metadata=schema.metadata,
        )

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        return {"output": None if schema is None else self._schema(schema)}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        target = self._schema(batch.schema)
        value_type = target.field(p.value_attribute).type
        base = batch.drop_columns(
            [c for c in [*p.attributes, p.name_attribute, p.value_attribute] if c in batch.column_names]
        )
        parts = []
        for attr in p.attributes:
            column = batch.column(attr)
            if column.type != value_type:
                column = column.cast(value_type)
            part = base.append_column(p.name_attribute, pa.repeat(pa.scalar(attr, pa.string()), batch.num_rows))
            part = part.append_column(p.value_attribute, column)
            if not p.keep_nulls:
                part = part.filter(pc.is_valid(column))
            parts.append(part)
        out = pa.concat_tables(parts)
        return {"output": with_crs(out.cast(target) if out.schema != target else out, get_crs(batch))}
