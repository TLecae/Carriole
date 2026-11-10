"""Code Python écrit dans un flux (PythonScript) : vérification, chargement, appel, et politique d'exécution.

Le même code sert dans le worker (poste de bureau) et dans le processus isolé du mode serveur (`etl_engine.sandbox`,
ADR 0053) : mêmes noms disponibles, mêmes messages d'erreur.
"""

from __future__ import annotations

import os
import traceback
from collections.abc import Callable, Mapping
from typing import Any

import pyarrow as pa

from etl_engine.sdk import REJECTED, ConfigError, reject

FILENAME = "<PythonScript>"
POLICIES = ("allow", "sandbox", "deny")


def policy() -> str:
    """`ETL_USER_CODE` : `allow` (dans le worker, droits du compte), `sandbox` (processus isolé), `deny` (refusé).
    Par défaut : `allow` sur le poste de bureau, `sandbox` en mode serveur."""
    value = os.environ.get("ETL_USER_CODE", "").strip().lower()
    if value in POLICIES:
        return value
    return "sandbox" if os.environ.get("ETL_MODE", "").strip().lower() == "server" else "allow"


def check(code: str) -> None:
    """Vérifie le code sans l'exécuter : syntaxe, présence de `process`, et politique d'exécution."""
    if not code.strip():
        raise ConfigError("code vide")
    try:
        compile(code, FILENAME, "exec")
    except SyntaxError as exc:
        raise ConfigError(f"erreur de syntaxe ligne {exc.lineno} : {exc.msg}") from exc
    if "def process" not in code:
        raise ConfigError("le code doit définir une fonction process(table, ctx)")
    if policy() == "deny":
        raise ConfigError("le code Python des flux est désactivé sur ce serveur (réglage ETL_USER_CODE)")
    if policy() == "sandbox":
        from etl_engine import sandbox

        sandbox.command("python")  # lève ConfigError si aucun bac à sable n'est disponible


def load(code: str) -> Callable[..., Any]:
    """Exécute la définition du code et renvoie sa fonction `process`."""
    import duckdb
    import numpy as np
    import pyarrow.compute as pc
    import shapely

    if not code.strip():
        raise ConfigError("code vide")
    try:
        compiled = compile(code, FILENAME, "exec")
    except SyntaxError as exc:
        raise ConfigError(f"erreur de syntaxe ligne {exc.lineno} : {exc.msg}") from exc
    namespace: dict[str, Any] = {"pa": pa, "pc": pc, "np": np, "shapely": shapely, "duckdb": duckdb, "reject": reject}
    try:
        exec(compiled, namespace)  # noqa: S102 - code de l'utilisateur (voir la politique d'exécution)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"le code ne se charge pas : {type(exc).__name__}: {exc}") from exc
    function = namespace.get("process")
    if not callable(function):
        raise ConfigError("le code doit définir une fonction process(table, ctx)")
    return function


def call(function: Callable[..., Any], batch: pa.Table, ctx: Any) -> dict[str, pa.Table]:
    """Appelle `process` sur un lot ; une erreur du code est rapportée avec sa ligne."""
    try:
        result = function(batch, ctx)
    except Exception as exc:  # noqa: BLE001 - toute erreur du code utilisateur est rapportée avec sa ligne
        line = next(
            (f.lineno for f in reversed(traceback.extract_tb(exc.__traceback__)) if f.filename == FILENAME), None
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
