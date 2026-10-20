"""ExplodeList : une entité par élément d'un attribut liste."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


class ExplodeList(Modifier):
    name = "ExplodeList"
    label = "Éclater une liste"
    category = "Attributs"
    description = "Produit une entité par élément d'un attribut de type liste (les autres attributs sont recopiés)."
    help = (
        "L'attribut doit être une liste (ex. issue d'un tableau PostgreSQL ou du SplitAttribute en mode liste). "
        "Une liste vide ou nulle ne produit aucune entité, sauf si « Garder les listes vides » est coché (une "
        "entité avec élément vide). « Attribut indice » numérote les éléments à partir de 0."
    )
    rejected = False

    class Params(Params):
        attribute: Attribute = Field(
            "", title="Attribut liste", description="Attribut de type liste à éclater (une entité par élément)."
        )
        element: str = Field("", title="Attribut élément", description="Vide = remplace l'attribut liste.")
        index: str = Field("", title="Attribut indice", description="Facultatif.")
        keep_empty: bool = Field(
            False,
            title="Garder les listes vides",
            description="Une liste vide ou nulle donne quand même une entité (élément vide) au lieu de disparaître.",
        )

    def _names(self) -> tuple[str, str]:
        if not self.params.attribute:
            raise ConfigError("attribut liste non renseigné")
        return self.params.attribute, self.params.element or self.params.attribute

    def output_schema(self, inputs):
        source, target = self._names()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if source not in schema.names:
            raise ConfigError(f"attribut absent : {source}")
        kind = schema.field(source).type
        if not (pa.types.is_list(kind) or pa.types.is_large_list(kind)):
            raise ConfigError(f"« {source} » n'est pas une liste (type {kind})")
        out = schema.remove(schema.get_field_index(source))
        field = pa.field(target, kind.value_type)
        out = out.remove(out.get_field_index(target)) if target in out.names else out
        out = out.append(field)
        if self.params.index and self.params.index not in out.names:
            out = out.append(pa.field(self.params.index, pa.int64()))
        return {"output": out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        source, target = self._names()
        if source not in batch.column_names:
            raise ConfigError(f"attribut absent : {source}")
        column = batch.column(source).combine_chunks()
        if not (pa.types.is_list(column.type) or pa.types.is_large_list(column.type)):
            raise ConfigError(f"« {source} » n'est pas une liste (type {column.type})")
        if self.params.keep_empty:  # une liste vide ou nulle devient une liste d'un élément nul
            empty = pc.fill_null(pc.equal(pc.list_value_length(column), 0), True)
            column = pc.if_else(empty, pa.array([[None]] * len(column), column.type), column)
        parents = pc.list_parent_indices(column)
        values = pc.list_flatten(column)
        rest = batch.drop_columns([source]).take(parents)
        if target in rest.column_names:
            rest = rest.drop_columns([target])
        out = rest.append_column(target, values)
        if self.params.index:
            lengths = pc.list_value_length(column).fill_null(0).to_numpy(zero_copy_only=False)
            out = out.append_column(self.params.index, pa.array(_positions(lengths), pa.int64()))
        return {"output": with_crs(out, get_crs(batch))}


def _positions(lengths) -> list[int]:
    out: list[int] = []
    for n in lengths:
        out.extend(range(int(n)))
    return out
