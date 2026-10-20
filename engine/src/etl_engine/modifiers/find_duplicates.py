"""FindDuplicates : sépare les premières occurrences des doublons."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params


class FindDuplicates(Modifier):
    name = "FindDuplicates"
    label = "Filtrer les doublons"
    category = "Filtres"
    description = "Envoie la première entité de chaque clé sur « Unique », les suivantes sur « Duplicate »."
    help = (
        "Deux entités sont des doublons si toutes les clés sont égales (deux valeurs vides sont égales). "
        "Sans clé, on compare tous les attributs (hors géométrie). L'ordre d'arrivée décide de la première."
    )
    outputs = ("Unique", "Duplicate")
    rejected = False
    stateful = True

    class Params(Params):
        keys: AttributeList = Field([], title="Clés", description="Vide = tous les attributs hors géométrie.")

    def __init__(self, params, ctx) -> None:
        super().__init__(params, ctx)
        self._seen: set[tuple] = set()

    def output_schema(self, inputs):
        schema = inputs.get("input")
        return {"Unique": schema, "Duplicate": schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        keys = list(self.params.keys) or [c for c in batch.column_names if c != "geometry"]
        missing = [k for k in keys if k not in batch.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {missing}")
        unique = np.zeros(batch.num_rows, dtype=bool)
        for i, key in enumerate(zip(*[batch.column(k).to_pylist() for k in keys], strict=True)):
            if key not in self._seen:
                self._seen.add(key)
                unique[i] = True
        return {"Unique": batch.filter(pa.array(unique)), "Duplicate": batch.filter(pa.array(~unique))}
