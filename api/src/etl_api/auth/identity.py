"""Identité d'une requête : nom, groupes, rôle d'administrateur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Identity:
    name: str
    display: str = ""
    groups: tuple[str, ...] = field(default_factory=tuple)
    admin: bool = False

    def principals(self) -> frozenset[str]:
        """Ce à quoi un partage peut s'adresser : l'utilisateur, ses groupes, tout le monde (`*`)."""
        return frozenset({"*", f"user:{self.name}", *(f"group:{g}" for g in self.groups)})

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display": self.display or self.name,
            "groups": list(self.groups),
            "admin": self.admin,
        }
