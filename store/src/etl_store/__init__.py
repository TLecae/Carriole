"""Métadonnées de l'atelier : flux, runs, événements."""

from etl_store.chats import ChatStore
from etl_store.components import store_components
from etl_store.config import Settings
from etl_store.connections import ConnectionStore, InvalidConnection
from etl_store.filestore import FileAccessError, FileStore, InvalidFlowFile
from etl_store.store import FINAL_STATUSES, Conflict, DuplicateComponent, NotFound, NothingToPublish, Store


def open_store(settings: Settings) -> Store:
    """Le stockage choisi par `settings.flow_storage` : fichiers (poste de bureau) ou base (mode serveur)."""
    if settings.flow_storage == "files":
        return FileStore(settings.url, settings.meta_dir / "recent_flows.json")
    return Store(settings.url)


__all__ = [
    "ChatStore",
    "FINAL_STATUSES",
    "Conflict",
    "DuplicateComponent",
    "ConnectionStore",
    "FileAccessError",
    "FileStore",
    "InvalidConnection",
    "InvalidFlowFile",
    "NotFound",
    "NothingToPublish",
    "Settings",
    "Store",
    "open_store",
    "store_components",
]
