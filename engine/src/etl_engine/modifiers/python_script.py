"""PythonScript : une fonction Python écrite dans le flux, appelée sur chaque lot d'entités."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import Field

from etl_engine import user_code
from etl_engine.sdk import Modifier, Params, PythonCode

TEMPLATE = '''def process(table, ctx):
    """table : un lot d'entités (pyarrow.Table, géométrie WKB dans la colonne "geometry").
    Renvoyer une table, ou {"output": table, "Rejected": autre_table}."""
    return table
'''


class PythonScript(Modifier):
    name = "PythonScript"
    label = "Code Python"
    category = "Calculs"
    description = "Exécute une fonction Python `process(table, ctx)` sur chaque lot d'entités."
    help = (
        "Écrire une fonction `process(table, ctx)` : `table` est un lot d'entités (pyarrow.Table ; la géométrie est la "
        "colonne `geometry`, en WKB). Elle renvoie une table (sortie « output »), ou un dictionnaire "
        '`{"output": t1, "Rejected": t2}` (pour rejeter, `reject(table, "motif")`), ou rien pour ne rien envoyer. '
        "Disponibles sans import : `pa` (pyarrow), `pc` (pyarrow.compute), `np`, `shapely`, `duckdb`, `reject`. `ctx` donne "
        '`ctx.info("…")`, `ctx.warning("…")` et `ctx.variables`. Le code est appelé lot après lot dans l\'ordre d\'arrivée ; '
        "un état peut se garder dans `ctx.state` (un dictionnaire). **Attention** : sur un poste, ce code s'exécute avec vos "
        "droits sur l'ordinateur, comme un plugin : ne l'utiliser que dans des flux de confiance. Sur un serveur, il "
        "tourne dans un processus isolé, sans accès aux fichiers ni au réseau (ou il est désactivé par "
        "l'administrateur). Le schéma de sortie n'est pas connu avant l'exécution."
    )
    stateful = True

    class Params(Params):
        code: PythonCode = Field(TEMPLATE, title="Code", description="Définit `process(table, ctx)`.")

    def __init__(self, params: Any, ctx: Any) -> None:
        super().__init__(params, ctx)
        self._function = None
        self._sandbox: Any = None
        if not hasattr(ctx, "state"):
            ctx.state = {}

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        # Compile (syntaxe, présence de `process`) sans rien exécuter sur des données ; refusé si la politique
        # d'exécution du serveur l'interdit (ETL_USER_CODE, ADR 0053).
        user_code.check(self.params.code)
        return {"output": None}

    def run(self, inputs: Mapping[str, Any], emit: Any) -> None:
        try:
            super().run(inputs, emit)
        finally:
            if self._sandbox is not None:
                self._sandbox.close()
                self._sandbox = None

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        if user_code.policy() == "sandbox":  # mode serveur : processus isolé
            from etl_engine.sandbox import SandboxedScript

            if self._sandbox is None:
                self._sandbox = SandboxedScript(self.params.code, dict(getattr(self.ctx, "variables", {}) or {}))
            outputs, messages = self._sandbox.call(batch)
            for kind, message in messages:
                (self.ctx.warning if kind == "warning" else self.ctx.info)(message)
            return outputs
        if self._function is None:
            self._function = user_code.load(self.params.code)
        return user_code.call(self._function, batch, self.ctx)
