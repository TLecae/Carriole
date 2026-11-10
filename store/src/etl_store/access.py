"""Droits du mode serveur (ADR 0054) : qui peut lire, modifier ou partager un flux ou une connexion.

- Le propriétaire (celui qui l'a créé) et les administrateurs ont tous les droits.
- Un partage donne `read` (ouvrir, lancer, inspecter ses runs ; pour une connexion : l'utiliser dans un flux) ou
  `write` (en plus : enregistrer, arrêter ses runs ; pour une connexion : la modifier) à un utilisateur
  (`user:nom`), à un groupe (`group:nom`) ou à tous (`*`).
- Sur le poste de bureau, un seul utilisateur : tous les droits, aucune vérification.

Partagé par l'API (identité de la requête) et le worker (propriétaire du run, groupes relus dans `users`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from etl_engine import settings as user_settings

KINDS = ("flow", "connection")
LEVEL = {"read": 1, "write": 2, "owner": 3}
DEFAULT_ADMINS = ("group:carriole-admins",)
PRINCIPAL = re.compile(r"^(\*|user:[^\s,]{1,100}|group:[^,]{1,100})$")


def server_mode() -> bool:
    return os.environ.get("ETL_MODE", "").strip().lower() == "server"


def admins() -> tuple[str, ...]:
    """`ETL_ADMINS` (noms et `group:…` séparés par des virgules), sinon `admins` de settings.json."""
    raw: Any = os.environ.get("ETL_ADMINS", "").strip() or user_settings.get("admins")
    if not raw:
        return DEFAULT_ADMINS
    items = raw.split(",") if isinstance(raw, str) else [str(v) for v in raw]
    return tuple(i.strip() for i in items if i.strip()) or DEFAULT_ADMINS


def is_admin(name: str, groups: tuple[str, ...] | list[str], among: tuple[str, ...] | None = None) -> bool:
    wanted = {a.lower() for a in (among if among is not None else admins())}
    return (
        name.lower() in wanted
        or f"user:{name}".lower() in wanted
        or any(f"group:{g}".lower() in wanted for g in groups)
    )


@dataclass(frozen=True)
class Who:
    name: str
    groups: tuple[str, ...] = field(default_factory=tuple)
    admin: bool = False

    def principals(self) -> frozenset[str]:
        return frozenset({"*", f"user:{self.name}", *(f"group:{g}" for g in self.groups)})


def who(store: Any, name: str) -> Who:
    """Le propriétaire d'un run ou d'une planification, avec les groupes de sa dernière connexion."""
    user = store.get_user(name) or {}
    groups = tuple(user.get("groups", []))
    return Who(name=name, groups=groups, admin=is_admin(name, groups))


def role(store: Any, person: Who, kind: str, object_id: str, owner: str) -> str | None:
    """`owner`, `write`, `read` ou `None` (rien : l'objet n'existe pas pour cette personne)."""
    if not server_mode() or person.admin or (owner and owner == person.name):
        return "owner"
    return store.best_share(kind, object_id, person.principals())


def allows(granted: str | None, need: str) -> bool:
    return granted is not None and LEVEL[granted] >= LEVEL[need]


def clean_shares(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Partages reçus de l'interface : principal valide, rôle connu, un seul rôle par principal (le plus fort)."""
    best: dict[str, str] = {}
    for item in items:
        principal = str(item.get("principal", "")).strip()
        level = str(item.get("role", "read"))
        if not PRINCIPAL.match(principal):
            raise ValueError(f"destinataire invalide : « {principal} » (user:nom, group:nom ou *)")
        if level not in ("read", "write"):
            raise ValueError(f"rôle inconnu : {level} (read ou write)")
        if principal not in best or LEVEL[level] > LEVEL[best[principal]]:
            best[principal] = level
    return [{"principal": p, "role": r} for p, r in sorted(best.items())]


def dumps(items: list[dict[str, str]]) -> str:
    return json.dumps(items, ensure_ascii=False)
