"""Conversations de l'assistant IA et clés d'API des fournisseurs (chiffrées comme les mots de passe de connexions)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from etl_store.models import AssistantKeyRow, ChatRow
from etl_store.store import NotFound, Store

MAX_MESSAGES = 400


def _card(row: ChatRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "provider": row.provider,
        "model": row.model,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ChatStore:
    def __init__(self, store: Store, key: bytes) -> None:
        self.store = store
        self._box = Fernet(key)

    # ------------------------------------------------------------------ conversations
    def list(self, owner: str) -> list[dict[str, Any]]:
        with self.store.session() as s:
            rows = s.scalars(
                select(ChatRow).where(ChatRow.owner == owner).order_by(ChatRow.updated_at.desc()).limit(100)
            )
            return [_card(r) for r in rows]

    def get(self, chat_id: str, owner: str) -> dict[str, Any]:
        with self.store.session() as s:
            row = s.get(ChatRow, chat_id)
            if row is None or row.owner != owner:
                raise NotFound(chat_id)
            return {**_card(row), "messages": json.loads(row.messages)}

    def create(self, owner: str, provider: str, model: str, title: str = "") -> dict[str, Any]:
        now = time.time()
        row = ChatRow(
            id=uuid.uuid4().hex,
            owner=owner,
            title=title[:200],
            provider=provider,
            model=model,
            created_at=now,
            updated_at=now,
        )
        with self.store.session() as s, s.begin():
            s.add(row)
        return {**_card(row), "messages": []}

    def append(self, chat_id: str, owner: str, messages: list[dict[str, Any]], title: str | None = None) -> None:
        with self.store.session() as s, s.begin():
            row = s.get(ChatRow, chat_id)
            if row is None or row.owner != owner:
                raise NotFound(chat_id)
            kept = [*json.loads(row.messages), *messages][-MAX_MESSAGES:]
            row.messages = json.dumps(kept, ensure_ascii=False)
            row.updated_at = time.time()
            if title and not row.title:
                row.title = title[:200]

    def set_settings(self, chat_id: str, owner: str, provider: str, model: str) -> None:
        with self.store.session() as s, s.begin():
            row = s.get(ChatRow, chat_id)
            if row is None or row.owner != owner:
                raise NotFound(chat_id)
            row.provider, row.model = provider, model

    def delete(self, chat_id: str, owner: str) -> None:
        with self.store.session() as s, s.begin():
            row = s.get(ChatRow, chat_id)
            if row is None or row.owner != owner:
                raise NotFound(chat_id)
            s.execute(delete(ChatRow).where(ChatRow.id == chat_id))

    # ------------------------------------------------------------------ clés des fournisseurs
    def has_key(self, provider: str) -> bool:
        with self.store.session() as s:
            row = s.get(AssistantKeyRow, provider)
            return bool(row and row.secret_enc)

    def get_key(self, provider: str) -> str:
        with self.store.session() as s:
            row = s.get(AssistantKeyRow, provider)
            if row is None or not row.secret_enc:
                return ""
            try:
                return self._box.decrypt(row.secret_enc.encode()).decode()
            except InvalidToken:
                raise ValueError("clé d'API illisible : la clé de chiffrement a changé, la saisir à nouveau") from None

    def set_key(self, provider: str, key: str) -> None:
        """Enregistre la clé (chaîne vide : l'efface)."""
        with self.store.session() as s, s.begin():
            row = s.get(AssistantKeyRow, provider)
            if not key:
                if row is not None:
                    s.delete(row)
                return
            token = self._box.encrypt(key.encode()).decode()
            if row is None:
                s.add(AssistantKeyRow(provider=provider, secret_enc=token, updated_at=time.time()))
            else:
                row.secret_enc, row.updated_at = token, time.time()
