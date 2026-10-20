"""Connexions nommées aux bases de données (PostgreSQL / PostGIS, Oracle).

Un flux ne contient que le **nom** de la connexion ; les paramètres (et le mot de passe) sont résolus au moment
de l'exécution par un *fournisseur*. L'API et le worker installent le fournisseur adossé à la base de métadonnées
(secrets chiffrés) ; sans lui (CLI, tests), on lit la variable d'environnement `ETL_CONN_<NOM>` : une URI
(`postgresql://utilisateur:mot_de_passe@hôte:5432/base`, `oracle://…@hôte:1521/service`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from etl_engine.sdk import ConfigError

# type de base -> (libellé, port par défaut)
KINDS: dict[str, tuple[str, int]] = {
    "postgres": ("PostgreSQL / PostGIS", 5432),
    "oracle": ("Oracle", 1521),
}
_SCHEMES = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "oracle": "oracle",
}


@dataclass(frozen=True)
class ConnectionInfo:
    name: str
    host: str = ""
    port: int = 5432
    dbname: str = ""  # Oracle : nom du service
    user: str = ""
    password: str = field(default="", repr=False)  # jamais dans les journaux
    sslmode: str = "prefer"  # PostgreSQL
    kind: str = "postgres"
    # réglages propres à un type de base (paramètres de l'URI)
    options: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def conninfo(self) -> str:
        """Chaîne de connexion libpq (mots-clés)."""

        def quote(value: str) -> str:
            return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

        parts = {
            "host": self.host,
            "port": str(self.port),
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
        }
        return " ".join(f"{k}={quote(v)}" for k, v in parts.items() if v)


Provider = Callable[[str], ConnectionInfo | None]
_providers: dict[str, Provider] = {}


def install_provider(provider: Provider, key: str = "default") -> None:
    """Installe un fournisseur de connexions (consulté avant l'environnement) ; `key` le remplace s'il existe."""
    _providers[key] = provider


def clear_providers() -> None:
    _providers.clear()


def from_uri(name: str, uri: str) -> ConnectionInfo:
    """Connexion décrite par une URI (`oracle://`) ou une chaîne libpq (PostgreSQL)."""
    scheme = uri.split("://", 1)[0].lower() if "://" in uri else "postgresql"
    kind = _SCHEMES.get(scheme)
    if kind is None:
        raise ConfigError(f"connexion « {name} » : type de base inconnu « {scheme} »")
    if kind == "postgres":
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(uri)
        return ConnectionInfo(
            name=name,
            host=parts.get("host", "") or "",
            port=int(parts.get("port", 5432) or 5432),
            dbname=parts.get("dbname", "") or "",
            user=parts.get("user", "") or "",
            password=parts.get("password", "") or "",
            sslmode=parts.get("sslmode", "prefer") or "prefer",
        )
    url = urlsplit(uri)
    options: dict[str, Any] = dict(parse_qsl(url.query))
    return ConnectionInfo(
        name=name,
        host=url.hostname or "",
        port=url.port or KINDS[kind][1],
        dbname=unquote(url.path.lstrip("/")),
        user=unquote(url.username or ""),
        password=unquote(url.password or ""),
        kind=kind,
        options=options,
    )


def _from_env(name: str) -> ConnectionInfo | None:
    key = "ETL_CONN_" + re.sub(r"\W", "_", name).upper()
    uri = os.environ.get(key)
    return from_uri(name, uri) if uri else None


def resolve(name: str) -> ConnectionInfo:
    if not name:
        raise ConfigError("connexion non renseignée")
    for provider in [*_providers.values(), _from_env]:
        info = provider(name)
        if info is not None:
            return info
    raise ConfigError(f"connexion inconnue : « {name} » (à créer dans « Connexions »)")
