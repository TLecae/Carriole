"""Ce que l'API met à la disposition de l'assistant : fichiers, runs et données du cache (lecture seule)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etl_assistant.tools import ToolError
from etl_engine import paths
from etl_engine.cli_tools import describe_file
from etl_engine.registry import Registry
from etl_engine.sdk import ConfigError
from etl_store import NotFound, Store

from etl_api import data_service as ds

RASTER_EXTENSIONS = {".tif", ".tiff", ".asc", ".jp2", ".vrt", ".nc"}
MAX_ENTRIES = 150


class ApiBackend:
    def __init__(self, registry: Registry, store: Store, duck: ds.Duck) -> None:
        self.registry = registry
        self.store = store
        self.duck = duck

    # ---------------------------------------------------------------- fichiers (garde-fous des modificateurs)
    def describe_file(self, path: str, layer: str | None, count: bool) -> dict[str, Any]:
        try:
            checked = paths.check(path, "read")
            if Path(checked).suffix.lower() in RASTER_EXTENSIONS:
                from etl_engine.raster import core

                return {"format": "raster", **{k: v for k, v in core.describe(checked).items() if k != "transform"}}
            return describe_file(checked, count, layer)
        except (ConfigError, OSError, ValueError) as exc:
            raise ToolError(f"illisible : {exc}") from None

    def list_dir(self, path: str) -> dict[str, Any]:
        try:
            if not path.strip():
                return {"roots": paths.browse_roots("read")}
            listing = paths.listing(path, "read", ())
        except ConfigError as exc:
            raise ToolError(str(exc)) from None
        entries = listing.get("entries", [])
        return {
            **listing,
            "entries": entries[:MAX_ENTRIES],
            "truncated": listing.get("truncated", False) or len(entries) > MAX_ENTRIES,
        }

    # ------------------------------------------------------------------ runs
    def run_info(self, run_id: str) -> dict[str, Any]:
        try:
            run = self.store.get_run(run_id, detail=False)
        except NotFound:
            raise ToolError(f"run introuvable : {run_id}") from None
        nodes: dict[str, str] = {}
        for event in self.store.events_after(run_id, 0, 100000, types=("node",)):
            nodes[event["node"]] = event.get("status", "")
        return {
            "id": run["id"],
            "flow": run["flow_name"],
            "status": run["status"],
            "error": run["error"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "nodes": nodes,
            "counts": self.store.last_counts(run_id),
        }

    def run_logs(self, run_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            self.store.get_run(run_id, detail=False)
        except NotFound:
            raise ToolError(f"run introuvable : {run_id}") from None
        events = self.store.events_after(run_id, 0, 5000, types=("log", "run_finished"))
        rank = {"ERROR": 0, "WARNING": 1}  # les erreurs et avertissements d'abord, puis le reste dans l'ordre
        events.sort(key=lambda e: rank.get(str(e.get("level", "")).upper(), 2))
        return [
            {
                "level": e.get("level") or e.get("status", ""),
                "node": e.get("node"),
                "message": e.get("message") or e.get("error") or "",
            }
            for e in events[:limit]
        ]

    # ------------------------------------------------------------------ données du cache
    def _port(self, run_id: str, node: str, port: str) -> ds.Port:
        try:
            return ds.open_port(self.duck, run_id, node, port)
        except ds.DataError as exc:
            raise ToolError(str(exc)) from None

    def port_info(self, run_id: str, node: str, port: str) -> dict[str, Any]:
        info = ds.info(self._port(run_id, node, port))
        return {
            "total": info["total"],
            "columns": [{"name": c["name"], "type": c["type"]} for c in info["columns"]],
            "geometry": info.get("geometry"),
        }

    def port_rows(self, run_id: str, node: str, port: str, limit: int) -> list[dict[str, Any]]:
        page = ds.rows(self._port(run_id, node, port), ds.Query(limit=limit))
        names = page["columns"]
        return [dict(zip(names, row, strict=False)) for row in page["rows"]]
