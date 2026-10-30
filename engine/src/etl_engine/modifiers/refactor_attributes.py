"""RefactorAttributes : crée, renomme et supprime des attributs dans un seul tableau de règles."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.expr import guarded, quote, sql_base, sql_columns
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
    version = 2  # 2 : la sortie garde le SCR (les sorties en cache, sans lui, sont recalculées)

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
        # rename_columns perd les métadonnées du schéma : le SCR avec elles
        table = table.replace_schema_metadata(batch.schema.metadata)
        return table, (pa.concat_tables(rejected, promote_options="default") if rejected else None)

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        return {"output": self._apply(schema.empty_table())[0].schema}

    def sql(self, relations, schemas):
        schema = schemas.get("input")
        if schema is None:
            return None
        out = self._apply(schema.empty_table())[0].schema
        rules = self.params.rules
        computed = [r.attribute for r in rules if r.action == "set"]
        # chaque expression doit rester dans la sortie, une seule fois et sous son nom : le contrôle l'y évalue
        if GEOM in computed or len(set(computed)) < len(computed) or any(c not in out.names for c in computed):
            return None
        if any(r.action != "set" and r.attribute in computed for r in rules):
            return None
        texts = [r.expression for r in rules if r.action == "set"]
        query = sql_base(relations["input"], schema, texts)
        present = list(schema.names)
        for r in rules:
            name = quote(r.attribute)
            if r.action == "set":
                if r.attribute in present:
                    query = f"SELECT * REPLACE ({guarded(r.expression)} AS {name}) FROM ({query})"
                else:
                    query = f"SELECT *, {guarded(r.expression)} AS {name} FROM ({query})"
                    present.append(r.attribute)
            elif r.action == "remove":
                query = f"SELECT * EXCLUDE ({name}) FROM ({query})"
                present.remove(r.attribute)
            else:
                query = f"SELECT * RENAME ({name} AS {quote(r.new_name)}) FROM ({query})"
                present[present.index(r.attribute)] = r.new_name
        return {"output": f"SELECT {sql_columns(out.names, schema, texts)} FROM ({query})"}

    def sql_checked(self) -> list[str]:
        return [r.attribute for r in self.params.rules if r.action == "set" and r.attribute]

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        out, rejected = self._apply(batch)
        result = {"output": out}
        if rejected is not None:
            result[REJECTED] = rejected
        return result
