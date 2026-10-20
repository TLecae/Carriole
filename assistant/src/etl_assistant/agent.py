"""Boucle d'agent : le modèle répond, appelle des outils de lecture (exécutés ici) ou des outils d'action (proposés).

`run_turn` est un générateur d'événements pour l'interface (flux SSE) :

    {"type": "thinking", "step": n}                       le modèle est interrogé (réflexion, avant le premier mot)
    {"type": "text", "delta": "…"}                       morceau de réponse
    {"type": "tool_start", "id", "name", "input", "summary"}   un outil de lecture commence (l'interface l'affiche en cours)
    {"type": "tool", "id", "name", "input", "ok", "summary"}   un outil de lecture a été appelé
    {"type": "action", "id", "name", "data"}             proposition à valider d'un clic
    {"type": "message", "message": {...}}                message à conserver dans l'historique (assistant ou résultats)
    {"type": "error", "message": "…"}                    erreur du fournisseur
    {"type": "done", "usage": {...}}

Après une proposition valide, la boucle s'arrête : le modèle attend la décision de l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from etl_assistant.providers import Provider, ProviderError, ToolCall
from etl_assistant.tools import ACTION_NAMES, TOOLS, Toolbox, ToolError

MAX_STEPS = 10
WAITING = "Proposal shown to the user, who decides with one click. Do not assume it was applied; stop and wait."


def _summary(name: str, args: dict[str, Any]) -> str:
    bits = [f"{k}={v}" for k, v in args.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) < 80]
    return f"{name}({', '.join(bits)})"


def run_turn(
    provider: Provider,
    model: str,
    system: str,
    history: list[dict[str, Any]],
    toolbox: Toolbox,
    max_tokens: int = 4096,
) -> Iterator[dict[str, Any]]:
    messages = list(history)
    usage = {"input": 0, "output": 0, "cache_read": 0}
    for step in range(MAX_STEPS):
        turn = None
        yield {"type": "thinking", "step": step}
        try:
            for kind, value in provider.stream(
                system=system, messages=messages, tools=TOOLS, model=model, max_tokens=max_tokens
            ):
                if kind == "text":
                    yield {"type": "text", "delta": value}
                else:
                    turn = value
        except ProviderError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        if turn is None:
            yield {"type": "error", "message": str(ProviderError("réponse vide du fournisseur"))}
            return
        for key, count in turn.usage.items():
            usage[key] = usage.get(key, 0) + count
        blocks: list[dict[str, Any]] = []
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in turn.tool_calls]
        if not blocks:
            blocks = [{"type": "text", "text": "…"}]
        assistant = {"role": "assistant", "content": blocks}
        messages.append(assistant)
        yield {"type": "message", "message": assistant}
        if not turn.tool_calls:
            break
        results: list[dict[str, Any]] = []
        waiting = False
        for call in turn.tool_calls:
            if call.name not in ACTION_NAMES:
                summary = _summary(call.name, call.input)
                yield {"type": "tool_start", "id": call.id, "name": call.name, "input": call.input, "summary": summary}
            result, is_error, action = _execute(call, toolbox)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result,
                    **({"is_error": True} if is_error else {}),
                }
            )
            if action is not None:
                waiting = True
                yield {"type": "action", "id": call.id, "name": call.name, "data": action}
            else:
                yield {
                    "type": "tool",
                    "id": call.id,
                    "name": call.name,
                    "input": call.input,
                    "ok": not is_error,
                    "summary": _summary(call.name, call.input),
                }
        reply = {"role": "user", "content": results}
        messages.append(reply)
        yield {"type": "message", "message": reply}
        if waiting:
            break
    yield {"type": "done", "usage": usage}


def _execute(call: ToolCall, toolbox: Toolbox) -> tuple[str, bool, dict[str, Any] | None]:
    """(texte du résultat, erreur ?, proposition à afficher)."""
    if call.error:
        return call.error, True, None
    try:
        if call.name in ACTION_NAMES:
            return WAITING, False, toolbox.propose(call.name, call.input)
        return toolbox.call_read(call.name, call.input), False, None
    except ToolError as exc:
        return str(exc), True, None
    except Exception as exc:  # noqa: BLE001 - un outil défaillant ne doit pas casser la conversation
        return f"échec de l'outil {call.name} : {type(exc).__name__} : {str(exc)[:300]}", True, None
