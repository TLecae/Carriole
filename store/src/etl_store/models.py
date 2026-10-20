"""Tables : flux, runs, événements."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FlowRow(Base):
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text)  # JSON du flux (voir ADR 0002)
    owner: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(Integer, default=1)  # contrôle de concurrence à l'enregistrement
    component_name: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)  # composant (schéma v4)


class ComponentVersionRow(Base):
    """Instantané immuable d'un composant, publié explicitement (schéma v6)."""

    __tablename__ = "component_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_component_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)  # JSON du flux avec ses composants imbriqués épinglés
    note: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[float] = mapped_column(Float)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    flow_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    flow_name: Mapped[str] = mapped_column(String(200))
    flow_json: Mapped[str] = mapped_column(Text)  # flux figé tel qu'exécuté
    params: Mapped[str] = mapped_column(Text, default="{}")
    options: Mapped[str] = mapped_column(Text, default="{}")  # cache, target, reuse_from
    status: Mapped[str] = mapped_column(String(20), index=True)  # queued running done error cancelled interrupted
    owner: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    heartbeat_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    worker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON : résultat par nœud
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    purged_at: Mapped[float | None] = mapped_column(Float, nullable=True)  # cache supprimé par la purge (schéma v2)


class EventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(30))
    node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[str] = mapped_column(Text)  # JSON complet de l'événement


class MetaRow(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class ConnectionRow(Base):
    """Connexion nommée à une base ; le mot de passe n'est stocké que chiffré (schéma v3)."""

    __tablename__ = "connections"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="postgres")
    host: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int] = mapped_column(Integer, default=5432)
    dbname: Mapped[str] = mapped_column(String(100), default="")
    user: Mapped[str] = mapped_column(String(100), default="")
    sslmode: Mapped[str] = mapped_column(String(20), default="prefer")
    options: Mapped[str] = mapped_column(Text, default="{}")  # JSON : réglages propres au type de base (v7)
    secret_enc: Mapped[str] = mapped_column(Text, default="")  # mot de passe chiffré (Fernet)
    owner: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)


class ScheduleRow(Base):
    """Planification : lance un flux enregistré selon une expression cron (schéma v5)."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    flow_id: Mapped[str] = mapped_column(String(32), index=True)
    cron: Mapped[str] = mapped_column(String(100))
    params: Mapped[str] = mapped_column(Text, default="{}")  # paramètres publiés du flux
    options: Mapped[str] = mapped_column(Text, default="{}")  # cache, notify_email, allow_overlap
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    owner: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)
    next_run_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    last_run_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChatRow(Base):
    """Conversation avec l'assistant IA (en local : un utilisateur, un poste)."""

    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    messages: Mapped[str] = mapped_column(Text, default="[]")  # JSON : messages au format interne de l'assistant
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)


class AssistantKeyRow(Base):
    """Clé d'API d'un fournisseur de modèle, chiffrée (Fernet, clé protégée par le coffre Windows)."""

    __tablename__ = "assistant_keys"

    provider: Mapped[str] = mapped_column(String(60), primary_key=True)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)
