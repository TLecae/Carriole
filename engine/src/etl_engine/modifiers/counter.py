"""Counter : numérote les entités, éventuellement par groupe."""

from __future__ import annotations

import pyarrow as pa
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params


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
        if self.params.attribute in schema.names:
            return {"output": schema}
        return {"output": schema.append(pa.field(self.params.attribute, pa.int64()))}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        missing = [g for g in p.group_by if g not in batch.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {missing}")
        if p.group_by:
            keys = zip(*[batch.column(g).to_pylist() for g in p.group_by], strict=True)
            values = []
            for key in keys:
                n = self._groups.get(key, p.start)
                values.append(n)
                self._groups[key] = n + p.step
        else:
            values = [self._next + i * p.step for i in range(batch.num_rows)]
            self._next += batch.num_rows * p.step
        column = pa.array(values, pa.int64())
        if p.attribute in batch.column_names:
            out = batch.set_column(batch.column_names.index(p.attribute), p.attribute, column)
        else:
            out = batch.append_column(p.attribute, column)
        return {"output": with_crs(out, get_crs(batch))}
