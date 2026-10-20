"""Client du backend local (`etl serve`) : le retrouver et l'interroger (ligne de commande, application)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl_engine import settings as user_settings


def info_path() -> Path:
    return user_settings.app_dirs().data / "server.json"


@dataclass
class ServerInfo:
    pid: int
    port: int
    token: str
    started: float

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def login_url(self) -> str:
        return f"{self.url}/?token={self.token}"

    def as_json(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "token": self.token,
            "started": self.started,
            "url": self.url,
            "login_url": self.login_url,
        }


class BackendError(Exception):
    """Le backend a répondu par une erreur, ou ne répond pas."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def read_info() -> ServerInfo | None:
    try:
        raw = json.loads(info_path().read_text(encoding="utf-8"))
        return ServerInfo(int(raw["pid"]), int(raw["port"]), str(raw["token"]), float(raw["started"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_healthy(info: ServerInfo, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{info.url}/api/health", timeout=timeout) as response:  # noqa: S310 - local
            return response.status == 200
    except OSError:
        return False


def running_server() -> ServerInfo | None:
    """Le backend de cet utilisateur s'il répond, sinon None."""
    info = read_info()
    return info if info and is_healthy(info) else None


def call(info: ServerInfo, method: str, path: str, body: Any = None, timeout: float = 30.0) -> Any:
    """Appel JSON de l'API ; `BackendError` avec le message du serveur en cas d'erreur."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-ETL-Token": info.token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{info.url}{path}", data=data, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail).get("detail", detail)
            detail = parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)
        except (ValueError, AttributeError):
            pass
        raise BackendError(detail or exc.reason, exc.code) from None
    except OSError as exc:
        raise BackendError(f"le backend ne répond pas : {exc}") from None
    return json.loads(raw) if raw else None


def stream_events(info: ServerInfo, path: str, body: Any, timeout: float = 600.0) -> Iterator[dict[str, Any]]:
    """Appel POST dont la réponse est un flux d'événements (SSE) : un dictionnaire par événement."""
    req = urllib.request.Request(  # noqa: S310 - local
        f"{info.url}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"X-ETL-Token": info.token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            for raw in response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data: "):
                    yield json.loads(line[6:])
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail).get("detail", detail)
            detail = parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)
        except (ValueError, AttributeError):
            pass
        raise BackendError(detail or exc.reason, exc.code) from None
    except OSError as exc:
        raise BackendError(f"le backend ne répond pas : {exc}") from None
