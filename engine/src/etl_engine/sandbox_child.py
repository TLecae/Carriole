"""Processus isolé qui exécute le code Python d'un flux (voir `etl_engine.sandbox`).

La sortie standard sert au protocole : `print()` du code de l'utilisateur va sur la sortie d'erreur.
"""

from __future__ import annotations

import os
import sys
from typing import Any


class ChildContext:
    """Ce que le code voit de `ctx` : messages (remontés au run), variables, état gardé d'un lot à l'autre."""

    def __init__(self, variables: dict[str, Any]) -> None:
        self.variables = variables
        self.state: dict[str, Any] = {}
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.messages.append(("info", str(message)))

    def warning(self, message: str) -> None:
        self.messages.append(("warning", str(message)))

    def drain(self) -> list[tuple[str, str]]:
        out, self.messages = self.messages, []
        return out


def main() -> None:
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    from etl_engine import sandbox, user_code
    from etl_engine.sdk import ConfigError

    source = sys.stdin.buffer
    function = None
    ctx = ChildContext({})
    while True:
        try:
            header, payload = sandbox.receive(source)
        except EOFError:
            return
        op = header.get("op")
        if op == "close":
            return
        try:
            if op == "init":
                ctx = ChildContext(dict(header.get("variables") or {}))
                function = user_code.load(str(header.get("code", "")))
                sandbox.send(protocol, {"ok": True})
            elif op == "batch" and function is not None:
                outputs = user_code.call(function, sandbox.from_ipc(payload), ctx)
                outputs = {p: t for p, t in outputs.items() if t is not None}
                sandbox.send(protocol, {"ok": True, "ports": list(outputs), "messages": ctx.drain()})
                for port, table in outputs.items():
                    sandbox.send(protocol, {"port": port}, sandbox.to_ipc(table))
            else:
                sandbox.send(protocol, {"error": f"demande inconnue : {op}"})
        except ConfigError as exc:
            sandbox.send(protocol, {"error": str(exc), "kind": "config", "messages": ctx.drain()})
        except Exception as exc:  # noqa: BLE001 - rapporté au worker, le fils reste prêt
            sandbox.send(protocol, {"error": str(exc), "messages": ctx.drain()})


if __name__ == "__main__":
    main()
