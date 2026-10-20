"""Accès aux métadonnées. Une instance par processus ; chaque méthode ouvre sa propre session."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from etl_engine import renames
from etl_engine.complib import NothingToPublish  # noqa: F401 - réexporté : même exception pour les deux stockages
from sqlalchemy import Engine, create_engine, delete, event, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from etl_store.models import Base, ComponentVersionRow, EventRow, FlowRow, MetaRow, RunRow, ScheduleRow

SCHEMA_VERSION = 7
FINAL_STATUSES = ("done", "error", "cancelled", "interrupted")


def _migrate_to_2(engine: Engine) -> None:
    """v2 : colonne `runs.purged_at` (une base v1 existante ne l'a pas ; une base neuve l'a déjà)."""
    if "purged_at" not in {c["name"] for c in inspect(engine).get_columns("runs")}:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE runs ADD COLUMN purged_at FLOAT"))


def _migrate_to_4(engine: Engine) -> None:
    """v4 : `flows.component_name` (un flux qui déclare `component` devient un composant réutilisable)."""
    if "component_name" not in {c["name"] for c in inspect(engine).get_columns("flows")}:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE flows ADD COLUMN component_name VARCHAR(60)"))
            con.execute(text("CREATE INDEX IF NOT EXISTS ix_flows_component_name ON flows (component_name)"))
    with engine.begin() as con:
        for row_id, content in con.execute(text("SELECT id, content FROM flows")).all():
            name = _component_name(json.loads(content))
            if name:
                con.execute(text("UPDATE flows SET component_name = :n WHERE id = :i"), {"n": name, "i": row_id})


def _migrate_to_7(engine: Engine) -> None:
    """v7 : `connections.options` (réglages propres à un type de base)."""
    if "options" not in {c["name"] for c in inspect(engine).get_columns("connections")}:
        with engine.begin() as con:
            con.execute(text("ALTER TABLE connections ADD COLUMN options TEXT DEFAULT '{}'"))


# Migrations ordonnées par version cible ; chacune doit pouvoir être rejouée sans dommage.
MIGRATIONS = {
    2: _migrate_to_2,
    3: lambda _engine: None,
    4: _migrate_to_4,
    5: lambda _engine: None,
    6: lambda _engine: None,
    7: _migrate_to_7,
}  # v3 : `connections`, v5 : `schedules`, v6 : `component_versions` (create_all)


def _component_name(content: dict[str, Any]) -> str | None:
    meta = content.get("component")
    return str(meta["name"]) if isinstance(meta, dict) and meta.get("name") else None


class Conflict(Exception):
    """Le flux a été modifié par quelqu'un d'autre depuis sa lecture."""


class DuplicateComponent(Exception):
    """Un autre flux porte déjà ce nom de composant."""


class NotFound(Exception):
    pass


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_connection, _):  # noqa: ANN001 - signature imposée par SQLAlchemy
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

    return engine


def _flow_dict(row: FlowRow, with_content: bool = True) -> dict[str, Any]:
    out = {
        "id": row.id,
        "name": row.name,
        "owner": row.owner,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "revision": row.revision,
        "component": row.component_name,
    }
    if with_content:
        out["flow"] = renames.migrate(json.loads(row.content))  # types renommés le 2026-10-20
    return out


def _flow_card(row: FlowRow) -> dict[str, Any]:
    """Résumé pour la page d'accueil : la description et la taille du flux, sans renvoyer tout son contenu."""
    content = json.loads(row.content)
    return {
        **_flow_dict(row, with_content=False),
        "description": content.get("description", "") or "",
        "node_count": len(content.get("nodes", [])),
    }


def _schedule_dict(row: ScheduleRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "flow_id": row.flow_id,
        "cron": row.cron,
        "params": json.loads(row.params),
        "options": json.loads(row.options),
        "enabled": row.enabled,
        "owner": row.owner,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "next_run_at": row.next_run_at,
        "last_run_at": row.last_run_at,
        "last_run_id": row.last_run_id,
        "last_error": row.last_error,
    }


def _run_dict(row: RunRow, detail: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "flow_id": row.flow_id,
        "flow_name": row.flow_name,
        "status": row.status,
        "owner": row.owner,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "heartbeat_at": row.heartbeat_at,
        "worker": row.worker,
        "error": row.error,
        "cancel_requested": row.cancel_requested,
        "options": json.loads(row.options),
        "params": json.loads(row.params),
        "purged_at": row.purged_at,
    }
    if detail:
        out["flow"] = json.loads(row.flow_json)
        out["result"] = json.loads(row.result) if row.result else None
    return out


class Store:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        self._session = sessionmaker(self.engine, expire_on_commit=False)
        self.init()

    def init(self) -> None:
        """Crée les tables manquantes puis applique les migrations jusqu'à la version courante."""
        fresh = not inspect(self.engine).has_table("meta")
        Base.metadata.create_all(self.engine)
        with self._session.begin() as s:
            row = s.get(MetaRow, "schema_version")
            version = SCHEMA_VERSION if row is None and fresh else int(row.value) if row else 1
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"base en version {version}, ce logiciel gère jusqu'à {SCHEMA_VERSION}")
        for target in sorted(MIGRATIONS):
            if version < target:
                MIGRATIONS[target](self.engine)
        with self._session.begin() as s:
            row = s.get(MetaRow, "schema_version")
            if row is None:
                s.add(MetaRow(key="schema_version", value=str(SCHEMA_VERSION)))
            else:
                row.value = str(SCHEMA_VERSION)

    def session(self) -> Session:
        return self._session()

    # ---------------------------------------------------------------- flux
    def components_provider(self):
        """Fournisseur de composants à installer dans le moteur (None : le moteur lit les dossiers de composants)."""
        from etl_store.components import store_components

        return store_components(self)

    def open_flow_file(self, path: str) -> dict[str, Any]:
        raise NotImplementedError("ce stockage n'enregistre pas les flux dans des fichiers")

    def create_flow(self, name: str, content: dict[str, Any], owner: str, path: str | None = None) -> dict[str, Any]:
        now = time.time()
        row = FlowRow(
            id=uuid.uuid4().hex,
            name=name,
            content=json.dumps(content, ensure_ascii=False),
            owner=owner,
            created_at=now,
            updated_at=now,
            revision=1,
            component_name=_component_name(content),
        )
        with self._session.begin() as s:
            self._check_component_name(s, row.component_name, None)
            s.add(row)
        return _flow_dict(row)

    def get_flow(self, flow_id: str) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(FlowRow, flow_id)
            if row is None:
                raise NotFound(flow_id)
            return _flow_dict(row)

    def list_flows(self) -> list[dict[str, Any]]:
        with self._session() as s:
            rows = s.scalars(select(FlowRow).order_by(FlowRow.updated_at.desc())).all()
            return [_flow_card(r) for r in rows]

    def update_flow(
        self, flow_id: str, name: str, content: dict[str, Any], revision: int | str | None
    ) -> dict[str, Any]:
        with self._session.begin() as s:
            row = s.get(FlowRow, flow_id)
            if row is None:
                raise NotFound(flow_id)
            if revision is not None and revision != row.revision:
                raise Conflict(f"révision {row.revision} sur le serveur, {revision} côté client")
            component = _component_name(content)
            self._check_component_name(s, component, flow_id)
            row.name, row.content, row.component_name = name, json.dumps(content, ensure_ascii=False), component
            row.updated_at, row.revision = time.time(), row.revision + 1
            return _flow_dict(row)

    @staticmethod
    def _check_component_name(s: Session, name: str | None, flow_id: str | None) -> None:
        if not name:
            return
        other = s.scalars(
            select(FlowRow.id).where(FlowRow.component_name == name, FlowRow.id != (flow_id or ""))
        ).first()
        if other:
            raise DuplicateComponent(f"un autre flux est déjà le composant « {name} » : choisir un autre nom")

    def list_components(self) -> list[dict[str, Any]]:
        """Flux déclarés comme composants (avec leur contenu), par nom."""
        with self._session() as s:
            rows = s.scalars(
                select(FlowRow).where(FlowRow.component_name.is_not(None)).order_by(FlowRow.component_name)
            ).all()
            return [_flow_dict(r) for r in rows]

    def get_component(self, name: str) -> dict[str, Any] | None:
        with self._session() as s:
            row = s.scalars(select(FlowRow).where(FlowRow.component_name == name)).first()
            return _flow_dict(row) if row else None

    # ---------------------------------------------------------------- versions de composants
    @staticmethod
    def _version_dict(row: ComponentVersionRow, with_content: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": row.name,
            "version": row.version,
            "note": row.note,
            "author": row.author,
            "created_at": row.created_at,
        }
        if with_content:
            out["flow"] = renames.migrate(json.loads(row.content))
        return out

    def publish_component(self, name: str, content: dict[str, Any], note: str, author: str) -> dict[str, Any]:
        """Nouvelle version (instantané de `content`) ; refuse si rien n'a changé depuis la précédente."""
        text_ = json.dumps(content, ensure_ascii=False, sort_keys=True)
        with self._session.begin() as s:
            last = s.scalars(
                select(ComponentVersionRow)
                .where(ComponentVersionRow.name == name)
                .order_by(ComponentVersionRow.version.desc())
            ).first()
            if last is not None and json.dumps(json.loads(last.content), ensure_ascii=False, sort_keys=True) == text_:
                raise NothingToPublish(f"la version {last.version} est identique au composant actuel")
            row = ComponentVersionRow(
                name=name,
                version=(last.version + 1) if last else 1,
                content=json.dumps(content, ensure_ascii=False),
                note=note,
                author=author,
                created_at=time.time(),
            )
            s.add(row)
            return self._version_dict(row)

    def list_component_versions(self, name: str) -> list[dict[str, Any]]:
        """Versions publiées d'un composant, de la plus récente à la plus ancienne (sans contenu)."""
        with self._session() as s:
            rows = s.scalars(
                select(ComponentVersionRow)
                .where(ComponentVersionRow.name == name)
                .order_by(ComponentVersionRow.version.desc())
            ).all()
            return [self._version_dict(r) for r in rows]

    def get_component_version(self, name: str, version: int | None = None) -> dict[str, Any] | None:
        """Une version publiée avec son contenu ; `version=None` : la dernière."""
        with self._session() as s:
            q = select(ComponentVersionRow).where(ComponentVersionRow.name == name)
            q = (
                q.where(ComponentVersionRow.version == version)
                if version is not None
                else q.order_by(ComponentVersionRow.version.desc())
            )
            row = s.scalars(q).first()
            return self._version_dict(row, with_content=True) if row else None

    def latest_component_versions(self) -> dict[str, int]:
        """Dernier numéro de version publié, par composant."""
        with self._session() as s:
            rows = s.execute(
                select(ComponentVersionRow.name, func.max(ComponentVersionRow.version)).group_by(
                    ComponentVersionRow.name
                )
            ).all()
            return {name: int(v) for name, v in rows}

    def delete_flow(self, flow_id: str) -> None:
        with self._session.begin() as s:
            if s.execute(delete(FlowRow).where(FlowRow.id == flow_id)).rowcount == 0:
                raise NotFound(flow_id)

    # ---------------------------------------------------------------- runs
    def create_run(
        self,
        flow: dict[str, Any],
        flow_id: str | None,
        params: dict[str, str],
        options: dict[str, Any],
        owner: str,
    ) -> dict[str, Any]:
        row = RunRow(
            id=uuid.uuid4().hex,
            flow_id=flow_id,
            flow_name=flow.get("name", "") or "(sans nom)",
            flow_json=json.dumps(flow, ensure_ascii=False),
            params=json.dumps(params, ensure_ascii=False),
            options=json.dumps(options, ensure_ascii=False),
            status="queued",
            owner=owner,
            created_at=time.time(),
        )
        with self._session.begin() as s:
            s.add(row)
        return _run_dict(row)

    def get_run(self, run_id: str, detail: bool = True) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(RunRow, run_id)
            if row is None:
                raise NotFound(run_id)
            return _run_dict(row, detail)

    def list_runs(
        self, status: str | None = None, owner: str | None = None, flow_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        stmt = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(RunRow.status.in_(status.split(",")))
        if owner:
            stmt = stmt.where(RunRow.owner == owner)
        if flow_id:
            stmt = stmt.where(RunRow.flow_id == flow_id)
        with self._session() as s:
            return [_run_dict(r, detail=False) for r in s.scalars(stmt).all()]

    def count_running(self, owner: str | None = None) -> int:
        stmt = select(func.count()).select_from(RunRow).where(RunRow.status == "running")
        if owner:
            stmt = stmt.where(RunRow.owner == owner)
        with self._session() as s:
            return int(s.scalar(stmt) or 0)

    def claim_next_run(self, worker: str, max_running: int = 0, max_per_user: int = 0) -> dict[str, Any] | None:
        """Réserve atomiquement le plus ancien run en attente, dans le respect des limites de concurrence.

        Un run dont le propriétaire a atteint sa limite est sauté : il garde sa place et un autre passe.
        """
        with self._session.begin() as s:
            if (
                max_running
                and (s.scalar(select(func.count()).select_from(RunRow).where(RunRow.status == "running")) or 0)
                >= max_running
            ):
                return None
            busy: dict[str, int] = {}
            if max_per_user:
                busy = dict(
                    s.execute(
                        select(RunRow.owner, func.count()).where(RunRow.status == "running").group_by(RunRow.owner)
                    ).all()
                )
            for candidate in s.scalars(
                select(RunRow).where(RunRow.status == "queued").order_by(RunRow.created_at).limit(50)
            ):
                if max_per_user and busy.get(candidate.owner, 0) >= max_per_user:
                    continue
                now = time.time()
                claimed = s.execute(
                    update(RunRow)
                    .where(RunRow.id == candidate.id, RunRow.status == "queued")
                    .values(status="running", worker=worker, started_at=now, heartbeat_at=now)
                ).rowcount
                if claimed:
                    row = s.get(RunRow, candidate.id, populate_existing=True)
                    return _run_dict(row)
        return None

    def heartbeat(self, run_id: str) -> None:
        with self._session.begin() as s:
            s.execute(update(RunRow).where(RunRow.id == run_id).values(heartbeat_at=time.time()))

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        """Annule un run en attente tout de suite, ou demande son arrêt s'il tourne."""
        with self._session.begin() as s:
            row = s.get(RunRow, run_id)
            if row is None:
                raise NotFound(run_id)
            if row.status == "queued":
                row.status, row.finished_at = "cancelled", time.time()
            elif row.status == "running":
                row.cancel_requested = True
            return _run_dict(row, detail=False)

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._session() as s:
            return bool(s.scalar(select(RunRow.cancel_requested).where(RunRow.id == run_id)))

    def finish_run(self, run_id: str, status: str, error: str | None, result: dict[str, Any] | None) -> None:
        with self._session.begin() as s:
            s.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(
                    status=status,
                    error=error,
                    finished_at=time.time(),
                    result=json.dumps(result, ensure_ascii=False) if result is not None else None,
                )
            )

    def interrupt_stale_runs(self, ttl: float) -> list[str]:
        """Les runs « running » sans heartbeat depuis `ttl` secondes (worker mort) passent en « interrompu »."""
        limit = time.time() - ttl
        with self._session.begin() as s:
            ids = list(
                s.scalars(
                    select(RunRow.id).where(RunRow.status == "running", func.coalesce(RunRow.heartbeat_at, 0) < limit)
                )
            )
            if ids:
                s.execute(
                    update(RunRow)
                    .where(RunRow.id.in_(ids))
                    .values(status="interrupted", finished_at=time.time(), error="worker arrêté pendant le run")
                )
        return ids

    # ---------------------------------------------------------------- purge du cache
    def runs_for_purge(self) -> list[dict[str, Any]]:
        """Runs terminés dont le cache existe encore, du plus ancien au plus récent."""
        stmt = (
            select(RunRow.id, RunRow.flow_id, RunRow.finished_at, RunRow.created_at)
            .where(RunRow.status.in_(FINAL_STATUSES), RunRow.purged_at.is_(None))
            .order_by(func.coalesce(RunRow.finished_at, RunRow.created_at))
        )
        with self._session() as s:
            return [
                {"id": r.id, "flow_id": r.flow_id, "at": r.finished_at or r.created_at} for r in s.execute(stmt).all()
            ]

    def protected_runs(self) -> set[str]:
        """Runs dont le cache sert encore : ceux en cours ou en attente, et ceux qu'ils reprennent (`reuse_from`)."""
        with self._session() as s:
            rows = s.execute(select(RunRow.id, RunRow.options).where(RunRow.status.in_(("queued", "running")))).all()
        keep: set[str] = set()
        for rid, options in rows:
            keep.add(rid)
            reuse = json.loads(options).get("reuse_from")
            if reuse:
                keep.add(reuse)
        return keep

    def mark_purged(self, run_ids: list[str]) -> None:
        if run_ids:
            with self._session.begin() as s:
                s.execute(update(RunRow).where(RunRow.id.in_(run_ids)).values(purged_at=time.time()))

    def known_run_ids(self) -> set[str]:
        with self._session() as s:
            return set(s.scalars(select(RunRow.id)))

    def try_acquire(self, key: str, interval: float) -> bool:
        """Verrou périodique partagé entre workers : vrai pour un seul d'entre eux par période de `interval` s."""
        now = time.time()
        try:
            with self._session.begin() as s:
                row = s.get(MetaRow, f"lock:{key}")
                if row is None:
                    s.add(MetaRow(key=f"lock:{key}", value=str(now)))
                    return True
                last = float(row.value)
                if now - last < interval:
                    return False
                claimed = s.execute(
                    update(MetaRow).where(MetaRow.key == row.key, MetaRow.value == row.value).values(value=str(now))
                ).rowcount
                return bool(claimed)
        except IntegrityError:
            return False  # un autre worker vient de créer le verrou (démarrage simultané) : c'est lui qui l'a

    # ---------------------------------------------------------------- planifications
    def create_schedule(self, data: dict[str, Any], owner: str, next_run_at: float | None) -> dict[str, Any]:
        now = time.time()
        row = ScheduleRow(
            id=uuid.uuid4().hex,
            name=data["name"],
            flow_id=data["flow_id"],
            cron=data["cron"],
            params=json.dumps(data.get("params", {}), ensure_ascii=False),
            options=json.dumps(data.get("options", {}), ensure_ascii=False),
            enabled=bool(data.get("enabled", True)),
            owner=owner,
            created_at=now,
            updated_at=now,
            next_run_at=next_run_at if data.get("enabled", True) else None,
        )
        with self._session.begin() as s:
            s.add(row)
        return _schedule_dict(row)

    def update_schedule(self, schedule_id: str, data: dict[str, Any], next_run_at: float | None) -> dict[str, Any]:
        with self._session.begin() as s:
            row = s.get(ScheduleRow, schedule_id)
            if row is None:
                raise NotFound(schedule_id)
            row.name, row.flow_id, row.cron = data["name"], data["flow_id"], data["cron"]
            row.params = json.dumps(data.get("params", {}), ensure_ascii=False)
            row.options = json.dumps(data.get("options", {}), ensure_ascii=False)
            row.enabled = bool(data.get("enabled", True))
            row.next_run_at = next_run_at if row.enabled else None
            row.updated_at = time.time()
            return _schedule_dict(row)

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(ScheduleRow, schedule_id)
            if row is None:
                raise NotFound(schedule_id)
            return _schedule_dict(row)

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._session() as s:
            return [_schedule_dict(r) for r in s.scalars(select(ScheduleRow).order_by(ScheduleRow.name))]

    def delete_schedule(self, schedule_id: str) -> None:
        with self._session.begin() as s:
            if s.execute(delete(ScheduleRow).where(ScheduleRow.id == schedule_id)).rowcount == 0:
                raise NotFound(schedule_id)

    def due_schedules(self, now: float) -> list[dict[str, Any]]:
        with self._session() as s:
            rows = s.scalars(
                select(ScheduleRow).where(
                    ScheduleRow.enabled.is_(True), ScheduleRow.next_run_at.is_not(None), ScheduleRow.next_run_at <= now
                )
            ).all()
            return [_schedule_dict(r) for r in rows]

    def mark_schedule_fired(
        self, schedule_id: str, run_id: str | None, fired_at: float, next_run_at: float | None, error: str | None
    ) -> None:
        with self._session.begin() as s:
            row = s.get(ScheduleRow, schedule_id)
            if row is None:
                return
            if run_id:
                row.last_run_at, row.last_run_id = fired_at, run_id
            row.next_run_at, row.last_error = next_run_at, error

    # ---------------------------------------------------------------- événements
    def add_events(self, run_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        with self._session.begin() as s:
            s.add_all(
                EventRow(
                    run_id=run_id,
                    ts=e.get("ts", time.time()),
                    type=e.get("type", ""),
                    node=e.get("node"),
                    payload=json.dumps(e, ensure_ascii=False, default=str),
                )
                for e in events
            )

    def events_after(
        self, run_id: str, after: int = 0, limit: int = 1000, types: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(EventRow).where(EventRow.run_id == run_id, EventRow.id > after).order_by(EventRow.id).limit(limit)
        if types:
            stmt = stmt.where(EventRow.type.in_(types))
        with self._session() as s:
            return [{"id": r.id, **json.loads(r.payload)} for r in s.scalars(stmt).all()]

    def last_counts(self, run_id: str) -> dict[str, int]:
        """Dernier comptage connu par « nœud/port » (pour repeindre l'éditeur à la reconnexion)."""
        out: dict[str, int] = {}
        for e in self.events_after(run_id, 0, 100000, types=("counts",)):
            out[f"{e['node']}/{e['port']}"] = e["count"]
        return out
