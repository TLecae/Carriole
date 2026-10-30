"""ExplodeList : une entité par élément d'un attribut liste."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.expr import quote
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


class ExplodeList(Modifier):
    name = "ExplodeList"
    label = "Éclater une liste"
    category = "Attributs"
    description = "Produit une entité par élément d'un attribut de type liste (les autres attributs sont recopiés)."
    help = (
        "L'attribut doit être une liste (ex. issue d'un tableau PostgreSQL ou de « Découper un texte » en mode liste). "
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
        if self.params.index:
            out = out.remove(out.get_field_index(self.params.index)) if self.params.index in out.names else out
            out = out.append(pa.field(self.params.index, pa.int64()))
        return {"output": out}

    def sql(self, relations, schemas):
        """Une entité par élément (`UNNEST`, dans l'ordre), l'indice par un second `UNNEST` de même longueur."""
        schema = schemas.get("input")
        if schema is None:
            return None
        self.output_schema(dict(schemas))  # mêmes contrôles que la voie par lots
        source, target = self._names()
        p = self.params
        values = quote(source)
        if p.keep_empty:  # liste vide ou nulle : un élément vide
            values = f"CASE WHEN len(coalesce({values}, [])) = 0 THEN [NULL] ELSE {values} END"
        kept = [quote(n) for n in schema.names if n not in {source, target, p.index}]
        columns = [*kept, f"UNNEST({values}) AS {quote(target)}"]
        if p.index:
            columns.append(f"UNNEST(range(len({values}))) AS {quote(p.index)}")
        return {"output": f"SELECT {', '.join(columns)} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        source, target = self._names()
        if source not in batch.column_names:
            raise ConfigError(f"attribut absent : {source}")
        column = batch.column(source).combine_chunks()
        if not (pa.types.is_list(column.type) or pa.types.is_large_list(column.type)):
            raise ConfigError(f"« {source} » n'est pas une liste (type {column.type})")
        if self.params.keep_empty:  # une liste vide ou nulle devient une liste d'un élément nul
            empty = pc.fill_null(pc.equal(pc.list_value_length(column), 0), True)
            one_null = pa.ListArray.from_arrays(
                pa.array(np.arange(len(column) + 1), pa.int32()), pa.nulls(len(column), column.type.value_type)
            )
            column = pc.if_else(empty, one_null.cast(column.type), column)
        parents = pc.list_parent_indices(column)
        values = pc.list_flatten(column)
        others = {target, self.params.index} - {source, ""}
        rest = batch.drop_columns([source, *(n for n in others if n in batch.column_names)])
        # la liste seule attribut : une table sans colonne n'a pas de lignes à répéter
        out = rest.take(parents).append_column(target, values) if rest.num_columns else pa.table({target: values})
        if self.params.index:
            lengths = pc.list_value_length(column).fill_null(0).to_numpy(zero_copy_only=False)
            starts = np.repeat(np.cumsum(lengths) - lengths, lengths)  # début de la liste de chaque élément
            out = out.append_column(self.params.index, pa.array(np.arange(len(values)) - starts, pa.int64()))
        return {"output": with_crs(out, get_crs(batch))}
