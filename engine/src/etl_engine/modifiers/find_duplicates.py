"""FindDuplicates : sépare les premières occurrences des doublons."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import group_codes, group_keys, group_ranks
from etl_engine.data import GEOM
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, Modifier, Params, missing_attributes, require_attributes


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
        require_attributes(schema, self.params.keys)
        return {"Unique": schema, "Duplicate": schema}

    def sql(self, relations, schemas):
        """La première entité de chaque valeur des clés (nul = nul) est unique : repérée sur les seules clés, numérotées
        dans l'ordre, puis recollée par position aux entités lues en flux (pas de tri des lignes entières)."""
        schema = schemas.get("input")
        if schema is None:
            return None
        rel = relations["input"]
        keys = list(self.params.keys) or [n for n in schema.names if n != GEOM]
        by = ", ".join(quote(k) for k in keys)
        numbered = f"SELECT row_number() OVER () AS __rn, {by} FROM {rel}"
        first = f"SELECT row_number() OVER (PARTITION BY {by} ORDER BY __rn) = 1 AS __first, __rn FROM ({numbered})"
        flags = f"SELECT __first FROM ({first}) ORDER BY __rn"
        joined = f"SELECT t.*, f.__first FROM {rel} t POSITIONAL JOIN ({flags}) f"
        return {
            "Unique": f"SELECT * EXCLUDE (__first) FROM ({joined}) WHERE __first",
            "Duplicate": f"SELECT * EXCLUDE (__first) FROM ({joined}) WHERE NOT __first",
        }

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        keys = list(self.params.keys) or [c for c in batch.column_names if c != GEOM]
        missing = [k for k in keys if k not in batch.column_names]
        if missing:
            raise missing_attributes(missing)
        unique = np.zeros(batch.num_rows, dtype=bool)
        codes, count = group_codes(batch, keys)
        _, first, _ = group_ranks(codes, count)  # première ligne de chaque valeur du lot
        for row, key in zip(first, group_keys(batch, keys, first), strict=True):
            if key not in self._seen:  # pas déjà vue dans un lot précédent
                self._seen.add(key)
                unique[row] = True
        return {"Unique": batch.filter(pa.array(unique)), "Duplicate": batch.filter(pa.array(~unique))}
