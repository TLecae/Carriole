"""SetVariables : pose des variables à partir des données, que les nœuds en aval lisent avec `${var:NOM}`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.sdk import Attribute, ConfigError, Emit, Modifier, Params

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class VariableSpec(Params):
    name: str = Field("", title="Nom de la variable", description="Lettres, chiffres et _ ; lue par ${var:NOM}.")
    source: Literal["attribute", "count"] = Field(
        "attribute",
        title="Valeur",
        description="attribute : la valeur d'un attribut ; count : le nombre d'entités reçues.",
    )
    attribute: Attribute = Field("", title="Attribut", description="Pour « attribute ».")
    pick: Literal["first", "last"] = Field(
        "first", title="Entité retenue", description="first : la première entité reçue ; last : la dernière."
    )
    default: str = Field(
        "", title="Valeur par défaut", description="Utilisée si l'attribut est vide ou s'il n'y a aucune entité."
    )


class SetVariables(Modifier):
    name = "SetVariables"
    label = "Poser des variables"
    category = "Lots et variables"
    description = "Lit une valeur dans les données (un attribut, ou le nombre d'entités) et la pose en variable."
    help = (
        "Les entités passent sans changement. Les variables posées ici s'utilisent dans les nœuds situés **en aval** "
        "avec ${var:NOM}, dans n'importe quel paramètre : un nœud démarre quand tout son amont est terminé, donc la "
        "valeur est connue. L'analyse refuse une variable que rien ne pose en amont. Exemple : un Agrégateur calcule "
        "la date la plus récente, SetVariables la pose en DATE_MAX, une lecture PostgreSQL filtre sur "
        "\"date\" > '${var:DATE_MAX}'."
    )
    rejected = False
    stateful = True  # « première » et « dernière » dépendent de l'ordre d'arrivée

    class Params(Params):
        variables: list[VariableSpec] = Field(
            default_factory=list, title="Variables", description="Une ligne par variable posée."
        )

    def _checked(self) -> list[VariableSpec]:
        specs = self.params.variables
        if not specs:
            raise ConfigError("au moins une variable est nécessaire")
        seen: set[str] = set()
        for spec in specs:
            if not _NAME.match(spec.name):
                raise ConfigError(f"nom de variable invalide : « {spec.name} » (lettres, chiffres et _)")
            if spec.name in seen:
                raise ConfigError(f"variable en double : {spec.name}")
            seen.add(spec.name)
            if spec.source == "attribute" and not spec.attribute:
                raise ConfigError(f"variable {spec.name} : attribut non renseigné")
        return specs

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._checked()
        return super().output_schema(inputs)

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        specs = self._checked()
        first: dict[str, Any] = {}
        last: dict[str, Any] = {}
        count = 0
        for batch in inputs.get("input", ()):
            self.ctx.check_cancel()
            if batch.num_rows == 0:
                continue
            for spec in specs:
                if spec.source != "attribute":
                    continue
                if spec.attribute not in batch.column_names:
                    raise ConfigError(f"attribut absent : {spec.attribute}")
                column = batch.column(spec.attribute)
                if spec.name not in first:
                    first[spec.name] = column[0].as_py()
                last[spec.name] = column[batch.num_rows - 1].as_py()
            count += batch.num_rows
            emit("output", batch)
        for spec in specs:
            if spec.source == "count":
                raw: Any = count
            else:
                raw = (first if spec.pick == "first" else last).get(spec.name)
            value = spec.default if raw is None or raw == "" else str(raw)
            self.ctx.set_variable(spec.name, value)
            self.ctx.info(f"${{var:{spec.name}}} = {value}")
