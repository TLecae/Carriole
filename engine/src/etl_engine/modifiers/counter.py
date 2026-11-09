"""Counter : numérote les entités, éventuellement par groupe."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import group_codes, group_keys, group_ranks
from etl_engine.data import get_crs, with_crs
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params, missing_attributes, require_attributes


class Counter(Modifier):
    name = "Counter"
    label = "Compteur"
    category = "Calculs"
    description = "Ajoute un numéro d'ordre à chaque entité, dans l'ordre d'arrivée (par groupe si demandé)."
    help = "Sans groupe : 1, 2, 3… Avec « Regrouper par » : la numérotation repart de « Début » pour chaque valeur."
    stateful = True
    rejected = False

    class Params(Params):
        attribute: str = Field("_count", title="Attribut créé", description="Nom de l'attribut qui reçoit le numéro.")
        start: int = Field(1, title="Début", description="Premier numéro attribué.")
        step: int = Field(1, title="Pas", description="Écart entre deux numéros successifs.")
        group_by: AttributeList = Field(
            [],
            title="Regrouper par",
            description="Vide : une seule numérotation ; sinon elle repart de « Début » pour chaque valeur.",
        )

    def __init__(self, params, ctx) -> None:
        super().__init__(params, ctx)
        self._next = params.start
        self._groups: dict[tuple, int] = {}

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if not self.params.attribute:
            raise ConfigError("nom d'attribut vide")
        if schema is None:
            return {"output": None}
        require_attributes(schema, self.params.group_by)
        if self.params.attribute in schema.names:
            field = pa.field(self.params.attribute, pa.int64())
            return {"output": schema.set(schema.get_field_index(field.name), field)}
        return {"output": schema.append(pa.field(self.params.attribute, pa.int64()))}

    def sql(self, relations, schemas):
        """Sans groupe : le numéro d'ordre de chaque entité. Avec des groupes (nul = nul) : le rang de l'entité dans son
        groupe, calculé sur les seules clés numérotées dans l'ordre, puis recollé par position aux entités lues en flux
        (pas de tri des lignes entières), comme `FindDuplicates`."""
        p = self.params
        schema = schemas.get("input")
        if schema is None:
            return None
        rel, start, step = relations["input"], int(p.start), int(p.step)
        if p.group_by:
            by = ", ".join(quote(g) for g in p.group_by)
            numbered = f"SELECT row_number() OVER () AS __rn, {by} FROM {rel}"
            ranks = f"SELECT row_number() OVER (PARTITION BY {by} ORDER BY __rn) AS __k, __rn FROM ({numbered})"
            rel = f"(SELECT t.*, k.__k FROM {rel} t POSITIONAL JOIN (SELECT __k FROM ({ranks}) ORDER BY __rn) k)"
            rank = "__k"
        else:
            rank = "row_number() OVER ()"
        value = f"(CAST({start} AS BIGINT) + ({rank} - 1) * {step})"
        if p.attribute in schema.names:
            columns = ", ".join(f"{value} AS {quote(n)}" if n == p.attribute else quote(n) for n in schema.names)
        else:
            columns = ", ".join([*(quote(n) for n in schema.names), f"{value} AS {quote(p.attribute)}"])
        return {"output": f"SELECT {columns} FROM {rel}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        missing = [g for g in p.group_by if g not in batch.column_names]
        if missing:
            raise missing_attributes(missing)
        if p.group_by:  # chaque valeur garde sa numérotation d'un lot à l'autre
            codes, count = group_codes(batch, p.group_by)
            rank, first, sizes = group_ranks(codes, count)
            keys = group_keys(batch, p.group_by, first)
            starts = np.array([self._groups.get(k, p.start) for k in keys], dtype=np.int64)
            values = starts[codes] + rank * p.step
            self._groups.update({k: int(s + n * p.step) for k, s, n in zip(keys, starts, sizes, strict=True)})
        else:
            values = self._next + np.arange(batch.num_rows, dtype=np.int64) * p.step
            self._next += batch.num_rows * p.step
        column = pa.array(values, pa.int64())
        if p.attribute in batch.column_names:
            out = batch.set_column(batch.column_names.index(p.attribute), p.attribute, column)
        else:
            out = batch.append_column(p.attribute, column)
        return {"output": with_crs(out, get_crs(batch))}
