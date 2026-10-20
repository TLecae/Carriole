"""Frontières d'un composant : ComponentInput et ComponentOutput définissent ses ports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import Field

from etl_engine import formats, paths
from etl_engine.sdk import ConfigError, Emit, FilePath, Modifier, Params


class ComponentInput(Modifier):
    name = "ComponentInput"
    label = "Entrée du composant"
    category = "Composants"
    description = "Définit une entrée du composant : les entités reçues par ce port arrivent ici."
    help = (
        "À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une "
        "entrée du nœud. Pour tester le composant seul, indiquer un fichier d'exemple : il n'est lu que si le flux "
        "est lancé directement (jamais quand il sert de composant)."
    )
    inputs = ()
    outputs = ("output",)
    rejected = False

    class Params(Params):
        port: str = Field("input", title="Nom du port")
        sample: FilePath = Field("", title="Fichier d'exemple", description="Pour tester le composant seul.")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        if not self.params.port.strip():
            raise ConfigError("nom de port vide")
        if self.params.sample:
            try:
                paths.check(self.params.sample, "read")
                return {"output": formats.read_schema(formats.detect(self.params.sample), self.params.sample)}
            except Exception:  # noqa: BLE001 - l'exemple est facultatif : schéma inconnu
                return {"output": None}
        return {"output": None}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        if not self.params.port.strip():
            raise ConfigError("nom de port vide")
        if not self.params.sample:
            self.ctx.warning(f"port « {self.params.port} » sans données : indiquer un fichier d'exemple pour tester")
            return
        fmt = formats.detect(paths.check(self.params.sample, "read"))
        for batch in formats.read_batches(fmt, self.params.sample, self.ctx.batch_rows):
            self.ctx.check_cancel()
            emit("output", batch)


class ComponentOutput(Modifier):
    name = "ComponentOutput"
    label = "Sortie du composant"
    category = "Composants"
    description = "Définit une sortie du composant : les entités reçues ici sortent par ce port."
    help = (
        "À poser dans un flux qui sert de composant. Dans le flux qui utilise le composant, ce port devient une "
        "sortie du nœud. Lancé seul, le nœud laisse simplement passer les entités (utile pour les inspecter)."
    )
    outputs = ("output",)
    rejected = False

    class Params(Params):
        port: str = Field("output", title="Nom du port")

    def output_schema(self, inputs):
        if not self.params.port.strip():
            raise ConfigError("nom de port vide")
        return {"output": inputs.get("input")}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        return {"output": batch}
