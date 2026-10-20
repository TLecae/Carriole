"""RefactorAttributes : crée, renomme et supprime des attributs dans un seul tableau de règles."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.modifiers.add_attributes import apply_expressions
from etl_engine.sdk import REJECTED, Attribute, ConfigError, Expression, Modifier, Params


class Rule(Params):
    action: Literal["set", "rename", "remove"] = Field(
        "set", title="Action", description="set : calculer ; rename : renommer ; remove : supprimer."
    )
    attribute: Attribute = Field(
        "", title="Attribut", description="set : à créer ou remplacer ; rename/remove : existant."
    )
    new_name: str = Field("", title="Nouveau nom", description="rename seulement.")
    expression: Expression = Field("", title="Expression", description='set seulement, ex. "surface" / 10000.')


class RefactorAttributes(Modifier):
    name = "RefactorAttributes"
    label = "Gérer des attributs"
    category = "Attributs"
    description = "Crée (expression), renomme ou supprime des attributs, règle après règle."
    help = (
        "Les règles s'appliquent dans l'ordre. « set » : calcule l'attribut par une expression SQL "
        '(ex. "surface" / 10000) ; « rename » : attribut → nouveau nom ; « remove » : supprime l\'attribut. '
        "Une entité dont l'expression échoue part sur « Rejected » avec le motif."
    )

    class Params(Params):
        rules: list[Rule] = Field([], title="Règles", description="Appliquées dans l'ordre.")

    def used_attributes(self) -> list[str]:
        # « set » crée l'attribut : seuls rename et remove visent un attribut existant
        return [r.attribute for r in self.params.rules if r.action != "set" and r.attribute]

    def _apply(self, batch: pa.Table) -> tuple[pa.Table, pa.Table | None]:
        table = batch
        rejected: list[pa.Table] = []
        for r in self.params.rules:
            if not r.attribute:
                raise ConfigError("chaque règle demande un attribut")
            if r.action == "set":
                if not r.expression:
                    raise ConfigError(f"règle « set » sur {r.attribute} : expression manquante")
                table, rej = apply_expressions(table, [(r.attribute, r.expression, "auto")])
                if rej is not None:
                    rejected.append(rej)
                continue
            if r.attribute not in table.column_names:
                raise ConfigError(f"attribut absent : {r.attribute}")
            if r.attribute == GEOM:
                raise ConfigError("la géométrie ne se renomme ni ne se supprime ici")
            if r.action == "remove":
                table = table.drop_columns([r.attribute])
            else:
                if not r.new_name:
                    raise ConfigError(f"règle « rename » sur {r.attribute} : nouveau nom manquant")
                if r.new_name in table.column_names and r.new_name != r.attribute:
                    raise ConfigError(f"« {r.new_name} » existe déjà")
                table = table.rename_columns([r.new_name if n == r.attribute else n for n in table.column_names])
        return table, (pa.concat_tables(rejected, promote_options="default") if rejected else None)

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        return {"output": self._apply(schema.empty_table())[0].schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        out, rejected = self._apply(batch)
        result = {"output": out}
        if rejected is not None:
            result[REJECTED] = rejected
        return result
