"""FindDuplicates : sépare les premières occurrences des doublons."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine import duck
from etl_engine.data import GEOM, plain_geometry
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
        self._con: Any = None  # clés déjà vues (voie par lots) : une table DuckDB, qui déborde sur disque au besoin

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

    def run(self, inputs, emit) -> None:
        try:
            super().run(inputs, emit)
        finally:
            if self._con is not None:
                self._con.close()
                self._con = None

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        """Premières occurrences d'un lot : premières du lot pour leur clé (nul = nul), et clé absente des lots
        précédents. Les clés vues sont gardées dans DuckDB (part du budget du run, débordement sur disque) : aucune
        boucle par entité, une mémoire bornée même quand presque toutes les clés sont différentes."""
        keys = list(self.params.keys) or [c for c in batch.column_names if c != GEOM]
        missing = [k for k in keys if k not in batch.column_names]
        if missing:
            raise missing_attributes(missing)
        cols = ", ".join(quote(k) for k in keys)
        if self._con is None:
            self._con = duck.connect("heavy")
            self._con.register("b", plain_geometry(batch.select(keys)))
            self._con.execute(f"CREATE TABLE seen AS SELECT {cols} FROM b LIMIT 0")
            self._con.unregister("b")
        con = self._con
        numbered = plain_geometry(batch.select(keys)).append_column(
            "__rn", pa.array(np.arange(batch.num_rows, dtype=np.int64))
        )
        con.register("b", numbered)
        try:
            same = " AND ".join(f"s.{quote(k)} IS NOT DISTINCT FROM f.{quote(k)}" for k in keys)
            first = f"SELECT __rn, {cols}, row_number() OVER (PARTITION BY {cols} ORDER BY __rn) AS __k FROM b"
            fresh = con.sql(
                f"SELECT f.__rn FROM ({first}) f WHERE f.__k = 1 AND NOT EXISTS (SELECT 1 FROM seen s WHERE {same})"
            ).to_arrow_table()
            con.register("fresh", fresh)
            con.execute(f"INSERT INTO seen SELECT {cols} FROM b WHERE __rn IN (SELECT __rn FROM fresh)")
            con.unregister("fresh")
        finally:
            con.unregister("b")
        unique = np.zeros(batch.num_rows, dtype=bool)
        unique[fresh.column(0).to_numpy()] = True
        return {"Unique": batch.filter(pa.array(unique)), "Duplicate": batch.filter(pa.array(~unique))}
