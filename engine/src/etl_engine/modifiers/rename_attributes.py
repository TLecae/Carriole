"""RenameAttributes : renomme des attributs."""

from __future__ import annotations

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


class Renaming(Params):
    source: Attribute = Field("", title="Ancien nom", description="Attribut à renommer.")
    target: str = Field("", title="Nouveau nom", description="Nouveau nom ; il ne doit pas déjà exister.")


class RenameAttributes(Modifier):
    name = "RenameAttributes"
    label = "Renommer des attributs"
    category = "Attributs"
    description = "Renomme des attributs : ancien nom, nouveau nom."
    help = "Les renommages s'appliquent ensemble (on peut échanger deux noms). Le nouveau nom ne doit pas déjà exister."

    class Params(Params):
        renamings: list[Renaming] = Field([], title="Renommages")

    def _mapping(self, names: list[str] | None) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for r in self.params.renamings:
            if not r.source or not r.target:
                raise ConfigError("chaque renommage demande un ancien et un nouveau nom")
            if GEOM in (r.source, r.target):
                raise ConfigError("la géométrie ne se renomme pas")
            mapping[r.source] = r.target
        if names is not None:
            missing = [s for s in mapping if s not in names]
            if missing:
                raise ConfigError(f"attributs absents : {missing}")
            final = [mapping.get(n, n) for n in names]
            dup = {n for n in final if final.count(n) > 1}
            if dup:
                raise ConfigError(f"noms en double après renommage : {sorted(dup)}")
        return mapping

    def _apply(self, table: pa.Table) -> pa.Table:
        mapping = self._mapping(table.column_names)
        return table.rename_columns([mapping.get(n, n) for n in table.column_names])

    def output_schema(self, inputs):
        schema = inputs.get("input")
        return {"output": self._apply(schema.empty_table()).schema if schema is not None else None}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        return {"output": self._apply(batch)}
