"""Assistant IA : fournisseurs, conversations, messages en flux (SSE), installation d'un modificateur.

Les clés d'API ne sortent jamais du serveur : elles sont chiffrées en base (clé protégée par le coffre Windows) et
l'interface ne voit que « clé enregistrée : oui/non ». Voir l'ADR 0027.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Annotated, Any

from etl_assistant import plugins, refs
from etl_assistant.agent import run_turn
from etl_assistant.privacy import MODES, default_mode, from_config
from etl_assistant.prompt import build_system
from etl_assistant.providers import KINDS, ProviderError, make_provider
from etl_assistant.tools import NAME_RE, Toolbox
from etl_engine import i18n, settings
from etl_engine.registry import Registry
from etl_store import ChatStore, NotFound, Settings
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from etl_api.assistant_backend import ApiBackend
from etl_api.deps import current_user, get_registry, get_settings, request_lang

router = APIRouter(tags=["assistant"])

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PRESETS = [
    {"id": "anthropic", "kind": "anthropic", "label": "Claude (Anthropic)", "base_url": "", "model": ""},
    {"id": "openai", "kind": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": ""},
    {
        "id": "openrouter",
        "kind": "openai",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
    },
    {
        "id": "ollama",
        "kind": "openai",
        "label": "Ollama (sur ce poste)",
        "base_url": "http://localhost:11434/v1",
        "model": "",
    },
]


def get_chats(request: Request) -> ChatStore:
    chats = getattr(request.app.state, "chats", None)
    if chats is None:
        chats = request.app.state.chats = ChatStore(request.app.state.store, request.app.state.settings.secret_key)
    return chats


Chats = Annotated[ChatStore, Depends(get_chats)]
User = Annotated[str, Depends(current_user)]
Lang = Annotated[str, Depends(request_lang)]


# ------------------------------------------------------------------------------------------------ fournisseurs
def _providers() -> dict[str, dict[str, Any]]:
    value = settings.get("assistant.providers")
    return value if isinstance(value, dict) else {}


def _public(provider_id: str, config: dict[str, Any], chats: ChatStore) -> dict[str, Any]:
    kind, base_url = config.get("kind", "openai"), config.get("base_url") or ""
    return {
        "id": provider_id,
        "kind": kind,
        "label": config.get("label") or provider_id,
        "base_url": base_url,
        "model": config.get("model") or "",
        "privacy": config.get("privacy") or default_mode(kind, base_url),
        "sample_rows": config.get("sample_rows", 5),
        "has_key": chats.has_key(provider_id),
    }


class ProviderIn(BaseModel):
    kind: str
    label: str = Field("", max_length=60)
    base_url: str = Field("", max_length=300)
    model: str = Field("", max_length=120)
    privacy: str = ""
    sample_rows: int = Field(5, ge=1, le=20)
    api_key: str | None = None  # None : conserver la clé actuelle ; chaîne vide : l'effacer


@router.get("/assistant/config")
def get_config(chats: Chats) -> dict[str, Any]:
    providers = [_public(pid, cfg, chats) for pid, cfg in _providers().items()]
    return {
        "providers": providers,
        "default": settings.get("assistant.default") or (providers[0]["id"] if providers else None),
        "presets": PRESETS,
        "modes": list(MODES),
    }


@router.put("/assistant/providers/{provider_id}")
def put_provider(provider_id: str, body: ProviderIn, chats: Chats) -> dict[str, Any]:
    if not ID_RE.match(provider_id):
        raise HTTPException(422, "identifiant de fournisseur invalide : minuscules, chiffres, « - » et « _ »")
    if body.kind not in KINDS:
        raise HTTPException(422, f"type de fournisseur inconnu : {body.kind}")
    if body.base_url and not re.match(r"^https?://", body.base_url):
        raise HTTPException(422, "l'adresse du fournisseur doit commencer par http:// ou https://")
    if body.privacy and body.privacy not in MODES:
        raise HTTPException(422, f"mode de confidentialité inconnu : {body.privacy}")
    providers = _providers()
    previous = providers.get(provider_id, {})
    config = {
        "kind": body.kind,
        "label": body.label,
        "base_url": body.base_url,
        "model": body.model,
        "privacy": body.privacy or default_mode(body.kind, body.base_url),
        "sample_rows": body.sample_rows,
    }
    if "script" in previous:  # fournisseur scripté des tests : conservé tel quel
        config["script"] = previous["script"]
    providers[provider_id] = config
    settings.set_value("assistant.providers", providers)
    if not settings.get("assistant.default"):
        settings.set_value("assistant.default", provider_id)
    if body.api_key is not None:
        chats.set_key(provider_id, body.api_key.strip())
    return _public(provider_id, config, chats)


@router.delete("/assistant/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, chats: Chats) -> None:
    providers = _providers()
    if provider_id not in providers:
        raise HTTPException(404, "fournisseur introuvable")
    del providers[provider_id]
    settings.set_value("assistant.providers", providers)
    chats.set_key(provider_id, "")
    if settings.get("assistant.default") == provider_id:
        settings.set_value("assistant.default", next(iter(providers), None))


@router.put("/assistant/default/{provider_id}")
def put_default(provider_id: str) -> dict[str, str]:
    if provider_id not in _providers():
        raise HTTPException(404, "fournisseur introuvable")
    settings.set_value("assistant.default", provider_id)
    return {"default": provider_id}


def _provider(provider_id: str, chats: ChatStore):
    config = _providers().get(provider_id)
    if config is None:
        raise HTTPException(404, "fournisseur introuvable : le configurer dans les réglages de l'assistant")
    try:
        key = chats.get_key(provider_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    kind = config.get("kind", "openai")
    if kind == "anthropic" and not key:
        raise HTTPException(409, "aucune clé d'API enregistrée pour ce fournisseur")
    return config, make_provider(kind, key, config.get("base_url") or None, config.get("script"))


@router.get("/assistant/providers/{provider_id}/models")
def list_models(provider_id: str, chats: Chats) -> dict[str, Any]:
    _, provider = _provider(provider_id, chats)
    try:
        return {"models": provider.models()}
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from None


# ------------------------------------------------------------------------------------------------ conversations
class ChatIn(BaseModel):
    provider: str
    model: str = ""


@router.get("/assistant/chats")
def list_chats(chats: Chats, user: User) -> list[dict[str, Any]]:
    return chats.list(user)


@router.post("/assistant/chats", status_code=201)
def create_chat(body: ChatIn, chats: Chats, user: User) -> dict[str, Any]:
    config, _ = _provider(body.provider, chats)
    return chats.create(user, body.provider, body.model or config.get("model") or "")


@router.get("/assistant/chats/{chat_id}")
def get_chat(chat_id: str, chats: Chats, user: User) -> dict[str, Any]:
    try:
        return chats.get(chat_id, user)
    except NotFound:
        raise HTTPException(404, "conversation introuvable") from None


@router.delete("/assistant/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, chats: Chats, user: User) -> None:
    try:
        chats.delete(chat_id, user)
    except NotFound:
        raise HTTPException(404, "conversation introuvable") from None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    references: list[dict[str, Any]] = []
    flow: dict[str, Any] = {}  # le flux ouvert dans l'éditeur
    run_id: str | None = None  # dernier run de ce flux
    notes: list[str] = []  # sort des propositions précédentes (appliquée, refusée, test échoué…)
    provider: str | None = None
    model: str | None = None


def _model_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Historique à envoyer au modèle : rôle et contenu seulement, sans appel d'outil resté sans réponse."""
    view = [{"role": m["role"], "content": m["content"]} for m in messages]
    while (
        view
        and view[-1]["role"] == "assistant"
        and any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            for b in (view[-1]["content"] if isinstance(view[-1]["content"], list) else [])
        )
    ):
        view.pop()
    return view


def _sse(event: dict[str, Any], lang: str) -> str:
    if event.get("type") == "error":
        event = {**event, "message": i18n.tr(event["message"], lang)}
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/assistant/chats/{chat_id}/messages")
def send_message(
    chat_id: str,
    body: MessageIn,
    request: Request,
    chats: Chats,
    user: User,
    lang: Lang,
    registry: Annotated[Registry, Depends(get_registry)],
) -> StreamingResponse:
    try:
        chat = chats.get(chat_id, user)
    except NotFound:
        raise HTTPException(404, "conversation introuvable") from None
    provider_id = body.provider or chat["provider"]
    config, provider = _provider(provider_id, chats)
    if provider_id != chat["provider"] or (body.model and body.model != chat["model"]):
        chats.set_settings(chat_id, user, provider_id, body.model or chat["model"])
    model = body.model or chat["model"] or config.get("model") or ""
    if not model:
        raise HTTPException(422, "aucun modèle choisi pour ce fournisseur")
    kind = config.get("kind", "openai")
    privacy = from_config(config, kind, config.get("base_url"))
    app = request.app
    toolbox = Toolbox(
        ApiBackend(registry, app.state.store, app.state.duck),
        flow=body.flow,
        run_id=body.run_id,
        privacy=privacy,
        lang=lang,
    )
    attached = refs.resolve(body.references, toolbox)
    content = "\n\n".join(part for part in (body.text, *body.notes, attached) if part)
    user_message = {"role": "user", "content": content, "ui": {"text": body.text, "references": body.references}}
    history = [*_model_view(chat["messages"]), {"role": "user", "content": content}]
    system = build_system(lang, registry, privacy)

    def stream() -> Iterator[str]:
        new = [user_message]
        saved = False

        def save() -> None:
            nonlocal saved
            if not saved:
                saved = True
                chats.append(chat_id, user, _complete(new), title=body.text.strip().splitlines()[0][:60])

        try:
            for event in run_turn(provider, model, system, history, toolbox):
                if event["type"] == "message":
                    new.append(event["message"])
                    continue
                if event["type"] in ("done", "error"):
                    save()  # avant le dernier événement : le client qui relit la conversation y trouve tout
                yield _sse(event, lang)
        finally:  # le client s'est déconnecté en route : la conversation garde quand même ce qui s'est passé
            save()

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _complete(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retire un dernier tour d'assistant dont les appels d'outils n'ont pas reçu de réponse (flux interrompu)."""
    last = messages[-1] if messages else None
    pending = (
        last is not None
        and last["role"] == "assistant"
        and isinstance(last["content"], list)
        and any(b.get("type") == "tool_use" for b in last["content"])
    )
    return messages[:-1] if pending else messages


# ---------------------------------------------------------------- modificateur écrit par l'assistant
class ModifierIn(BaseModel):
    name: str
    code: str = Field(max_length=60000)
    test_code: str = Field(max_length=60000)


@router.post("/assistant/modifiers")
def install_modifier(
    body: ModifierIn, request: Request, cfg: Annotated[Settings, Depends(get_settings)]
) -> dict[str, Any]:
    """Teste le plugin dans un processus isolé, puis l'installe s'il passe. Appelé sur le clic de l'utilisateur."""
    if not NAME_RE.match(body.name):
        raise HTTPException(422, "nom de modifier invalide : CamelCase, lettres et chiffres")
    if body.name in request.app.state.registry:
        raise HTTPException(409, f"un modificateur nommé {body.name} existe déjà")
    result = plugins.run_tests(body.name, body.code, body.test_code)
    if not result["ok"]:
        return {"installed": False, "output": result["output"]}
    try:
        target = plugins.install(body.name, body.code, cfg.plugins_dir)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from None
    request.app.state.registry = Registry.discover(cfg.plugins_dir)  # la palette le montre dès le prochain affichage
    ok = body.name in request.app.state.registry
    return {
        "installed": ok,
        "path": str(target),
        "output": result["output"]
        if ok
        else "le plugin n'a pas pu être chargé : " + str(request.app.state.registry.errors),
    }
