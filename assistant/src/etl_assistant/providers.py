"""Fournisseurs de modèles : Anthropic (SDK officiel), compatibles OpenAI (OpenAI, OpenRouter, Ollama…) et un faux
fournisseur scripté pour les tests.

Format interne des messages = celui d'Anthropic : `{"role": "user"|"assistant", "content": str | [blocs]}`, blocs
`text`, `tool_use` (`id`, `name`, `input`) et `tool_result` (`tool_use_id`, `content`). L'adaptateur OpenAI le convertit.
`stream()` produit `("text", morceau)` puis `("end", Turn)`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

KINDS = ("anthropic", "openai", "fake")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    error: str = ""  # arguments illisibles : renvoyé au modèle comme erreur d'outil


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)


class Provider(Protocol):
    kind: str

    def stream(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, max_tokens: int
    ) -> Iterator[tuple[str, Any]]: ...

    def models(self) -> list[str]: ...


class ProviderError(Exception):
    """Erreur du fournisseur présentable à l'utilisateur (clé refusée, modèle inconnu, réseau…)."""


def _explain(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return ProviderError("clé refusée par le fournisseur")
    if status == 404:
        return ProviderError("modèle ou adresse introuvable chez le fournisseur")
    if status == 429:
        return ProviderError("limite de débit atteinte chez le fournisseur : réessayer dans un moment")
    name = type(exc).__name__
    if "Connection" in name or "Timeout" in name:
        return ProviderError(f"fournisseur injoignable ({name}) : vérifier l'adresse, le réseau et le proxy")
    return ProviderError(f"{name} : {str(exc)[:300]}")


# ------------------------------------------------------------------------------------------------ Anthropic
class AnthropicProvider:
    kind = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None, max_retries=2, timeout=120.0)

    def stream(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, max_tokens: int
    ) -> Iterator[tuple[str, Any]]:
        # mise en cache du prompt système et des outils : identiques d'un tour à l'autre
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]
        if specs:
            specs[-1] = {**specs[-1], "cache_control": {"type": "ephemeral"}}
        try:
            with self.client.messages.stream(
                model=model, max_tokens=max_tokens, system=system_blocks, messages=messages, tools=specs
            ) as stream:
                yield from (("text", chunk) for chunk in stream.text_stream)
                final = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - le SDK lève des erreurs de plusieurs familles
            raise _explain(exc) from exc
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in final.content if b.type == "tool_use"]
        text = "".join(b.text for b in final.content if b.type == "text")
        usage = {
            "input": final.usage.input_tokens,
            "output": final.usage.output_tokens,
            "cache_read": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
        }
        yield "end", Turn(text, calls, final.stop_reason or "end_turn", usage)

    def models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list(limit=100)]
        except Exception as exc:  # noqa: BLE001
            raise _explain(exc) from exc


# ------------------------------------------------------------------------------------------------ compatibles OpenAI
def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conversion du format interne vers l'API « chat completions »."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if b["type"] == "tool_use"
            ]
            message: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            out.append(message)
        else:
            for b in content:  # les résultats d'outils d'abord : ils suivent immédiatement l'appel
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b["content"])})
            texts = "".join(b["text"] for b in content if b["type"] == "text")
            if texts:
                out.append({"role": "user", "content": texts})
    return out


class OpenAIProvider:
    kind = "openai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        import openai

        # Ollama n'a pas de clé : le SDK en exige une non vide
        self.client = openai.OpenAI(api_key=api_key or "none", base_url=base_url or None, max_retries=2, timeout=120.0)

    def stream(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, max_tokens: int
    ) -> Iterator[tuple[str, Any]]:
        specs = [
            {
                "type": "function",
                "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
            }
            for t in tools
        ]
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = "stop"
        try:
            stream = self.client.chat.completions.create(
                model=model,
                messages=to_openai_messages(system, messages),
                tools=specs or None,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield "text", delta.content
                for part in delta.tool_calls or []:
                    slot = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = part.id or slot["id"]
                    if part.function:
                        slot["name"] += part.function.name or ""
                        slot["arguments"] += part.function.arguments or ""
                finish = choice.finish_reason or finish
        except Exception as exc:  # noqa: BLE001
            raise _explain(exc) from exc
        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
                error = "" if isinstance(arguments, dict) else "les arguments de l'outil doivent être un objet JSON"
            except json.JSONDecodeError:
                arguments, error = {}, "arguments de l'outil illisibles (JSON invalide)"
            tool_calls.append(
                ToolCall(slot["id"] or f"call_{index}", slot["name"], arguments if not error else {}, error)
            )
        yield "end", Turn("".join(text_parts), tool_calls, "tool_use" if tool_calls else finish)

    def models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except Exception as exc:  # noqa: BLE001
            raise _explain(exc) from exc


# ------------------------------------------------------------------------------------------------ faux fournisseur
class FakeProvider:
    """Fournisseur scripté, sans réseau : pour les tests et la CI.

    `script` : liste de scénarios `{"match": "texte", "turns": [{"text": "…", "tool_calls": [{"name", "input"}]}]}`.
    Le scénario retenu est le premier dont `match` figure dans le dernier message de l'utilisateur (hors résultats
    d'outils) ; le tour joué est le n-ième, n = nombre de réponses de l'assistant depuis ce message. Aucun état : une
    requête rejouée donne la même réponse.
    """

    kind = "fake"

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = script or []
        self.calls: list[dict[str, Any]] = []  # ce que le modèle a reçu (les tests le consultent)

    @staticmethod
    def _text_of(message: dict[str, Any]) -> str:
        content = message["content"]
        if isinstance(content, str):
            return content
        return "".join(b.get("text", "") for b in content if b["type"] == "text")

    def stream(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, max_tokens: int
    ) -> Iterator[tuple[str, Any]]:
        self.calls.append(
            {"system": system, "messages": list(messages), "tools": [t["name"] for t in tools], "model": model}
        )
        # le dernier message qui contient du texte (pas un simple résultat d'outil)
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user" and self._text_of(m))
        question = self._text_of(messages[last_user])
        played = sum(1 for m in messages[last_user + 1 :] if m["role"] == "assistant")
        for scenario in self.script:
            if scenario.get("match", "") in question:
                turns = scenario["turns"]
                spec = turns[min(played, len(turns) - 1)]
                break
        else:
            spec = {"text": "Je n'ai pas de réponse scriptée pour cette question."}
        text = spec.get("text", "")
        for start in range(0, len(text), 12):
            yield "text", text[start : start + 12]
        calls = [
            ToolCall(f"call_{len(messages)}_{n}", c["name"], dict(c.get("input", {})))
            for n, c in enumerate(spec.get("tool_calls", []))
        ]
        yield "end", Turn(text, calls, "tool_use" if calls else "end_turn")

    def models(self) -> list[str]:
        return ["fake-1"]


def make_provider(
    kind: str, api_key: str = "", base_url: str | None = None, script: list[dict[str, Any]] | None = None
) -> Provider:
    if kind == "anthropic":
        return AnthropicProvider(api_key, base_url)
    if kind == "openai":
        return OpenAIProvider(api_key, base_url)
    if kind == "fake":
        return FakeProvider(script)
    raise ProviderError(f"type de fournisseur inconnu : {kind}")
