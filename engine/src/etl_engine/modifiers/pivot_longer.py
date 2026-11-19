"""PivotLonger : des colonnes vers des lignes."""

from __future__ import annotations

import numpy as np
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
        "valeur ; les entités produites par une même entité se suivent, dans l'ordre des attributs. Si les colonnes n'ont pas le même type, les valeurs deviennent du texte. Les valeurs vides sont ignorées "
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
        n, k = batch.num_rows, len(p.attributes)
        columns = [
            batch.column(a).cast(value_type) if batch.column(a).type != value_type else batch.column(a)
            for a in p.attributes
        ]
        stacked = pa.chunked_array([chunk for column in columns for chunk in column.chunks], value_type)
        # ligne par ligne : les k valeurs d'une entité se suivent (l'ordre ne dépend pas de la taille des lots) ;
        # la valeur de l'attribut j de la ligne i est à j * n + i dans `stacked`
        rows, which = np.divmod(np.arange(n * k, dtype=np.int64), k)
        values = stacked.take(pa.array(which * n + rows))
        names = pa.array(p.attributes, pa.string()).take(pa.array(which))
        kept = base.take(pa.array(rows))  # sans colonne (tout est dépivoté) : la table se construit quand même
        out = pa.Table.from_arrays(
            [*kept.columns, names, values], names=[*kept.column_names, p.name_attribute, p.value_attribute]
        )
        if not p.keep_nulls:
            out = out.filter(pc.is_valid(values))
        return {"output": with_crs(out.cast(target) if out.schema != target else out, get_crs(batch))}
