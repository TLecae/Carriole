"""PythonScript : une fonction Python écrite dans le flux, appelée sur chaque lot d'entités."""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.sdk import REJECTED, ConfigError, Modifier, Params, PythonCode, reject

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
        "un état peut se garder dans `ctx.state` (un dictionnaire). **Attention** : ce code s'exécute avec vos droits sur "
        "l'ordinateur, comme un plugin : ne l'utiliser que dans des flux de confiance. Le schéma de sortie n'est pas "
        "connu avant l'exécution."
    )
    stateful = True

    class Params(Params):
        code: PythonCode = Field(TEMPLATE, title="Code", description="Définit `process(table, ctx)`.")

    def __init__(self, params: Any, ctx: Any) -> None:
        super().__init__(params, ctx)
        self._function = None
        if not hasattr(ctx, "state"):
            ctx.state = {}

    def _compile(self) -> Any:
        if self._function is not None:
            return self._function
        code = self.params.code
        if not code.strip():
            raise ConfigError("code vide")
        try:
            compiled = compile(code, "<PythonScript>", "exec")
        except SyntaxError as exc:
            raise ConfigError(f"erreur de syntaxe ligne {exc.lineno} : {exc.msg}") from exc
        namespace: dict[str, Any] = {
            "pa": pa,
            "pc": pc,
            "np": np,
            "shapely": shapely,
            "duckdb": duckdb,
            "reject": reject,
        }
        try:
            exec(compiled, namespace)  # noqa: S102 - code de l'utilisateur, avec ses droits (voir l'aide)
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"le code ne se charge pas : {type(exc).__name__}: {exc}") from exc
        function = namespace.get("process")
        if not callable(function):
            raise ConfigError("le code doit définir une fonction process(table, ctx)")
        self._function = function
        return function

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        # Compile (syntaxe, présence de `process`) sans rien exécuter sur des données.
        code = self.params.code
        if not code.strip():
            raise ConfigError("code vide")
        try:
            compile(code, "<PythonScript>", "exec")
        except SyntaxError as exc:
            raise ConfigError(f"erreur de syntaxe ligne {exc.lineno} : {exc.msg}") from exc
        if "def process" not in code:
            raise ConfigError("le code doit définir une fonction process(table, ctx)")
        return {"output": None}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        function = self._compile()
        try:
            result = function(batch, self.ctx)
        except Exception as exc:  # noqa: BLE001 - toute erreur du code utilisateur est rapportée avec sa ligne
            line = next(
                (f.lineno for f in reversed(traceback.extract_tb(exc.__traceback__)) if f.filename == "<PythonScript>"),
                None,
            )
            where = f" (ligne {line})" if line else ""
            raise RuntimeError(f"{type(exc).__name__}: {exc}{where}") from exc
        if result is None:
            return {}
        if isinstance(result, pa.Table):
            return {"output": result}
        if isinstance(result, Mapping):
            known = {"output", REJECTED}
            unknown = [k for k in result if k not in known]
            if unknown:
                raise ConfigError(f"sortie inconnue : {', '.join(map(str, unknown))} (output ou Rejected)")
            for key, value in result.items():
                if not isinstance(value, pa.Table):
                    raise ConfigError(f"la sortie {key} doit être une pyarrow.Table")
            return dict(result)
        raise ConfigError("process doit renvoyer une pyarrow.Table, un dictionnaire de tables ou None")
