"""RouteByExpression : plusieurs conditions ordonnées, une sortie nommée par condition."""

from __future__ import annotations

import re
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import BaseModel, Field

from etl_engine.expr import safe_mask
from etl_engine.sdk import REJECTED, ConfigError, Expression, Modifier, Params, reject

FAILED = "Failed"
_PORT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_RESERVED = {FAILED, REJECTED, "input"}


class FilterBranch(Params):
    output: str = Field("", title="Sortie", description="Nom du port de sortie (lettres, chiffres et _).")
    condition: Expression = Field("", title="Condition", description='Expression SQL booléenne, ex. "pop" >= 1000.')


class RouteByExpression(Modifier):
    name = "RouteByExpression"
    label = "Filtre à plusieurs sorties"
    category = "Filtres"
    description = "Répartit les entités sur plusieurs sorties selon des conditions ordonnées."
    help = (
        "Chaque ligne donne une sortie et sa condition (expression SQL booléenne). Avec « first », une entité part sur la "
        "sortie de la **première** condition qu'elle vérifie ; avec « all », sur toutes celles qu'elle vérifie (elle est "
        "alors dupliquée). Celles qui ne vérifient aucune condition partent sur « Failed ». Une condition nulle compte "
        "comme fausse ; une entité dont l'évaluation échoue part sur « Rejected » avec le motif. Les sorties du nœud "
        "suivent la liste des conditions."
    )
    outputs = ("Passed", FAILED)

    class Params(Params):
        tests: list[FilterBranch] = Field(
            default_factory=lambda: [FilterBranch(output="Passed", condition="")],
            title="Conditions",
            description="Une sortie par ligne, dans l'ordre d'évaluation.",
            json_schema_extra={
                "default": [{"output": "Passed", "condition": ""}]
            },  # l'éditeur montre la ligne par défaut
        )
        mode: Literal["first", "all"] = Field(
            "first",
            title="Entité vérifiant plusieurs conditions",
            description="first : première sortie seulement ; all : toutes les sorties (l'entité est dupliquée).",
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if params is None:
            return cls.inputs, ("Passed", FAILED, REJECTED)
        names = [t.output for t in getattr(params, "tests", []) if isinstance(t, FilterBranch) and t.output]
        return cls.inputs, (*dict.fromkeys(names), FAILED, REJECTED)

    def _cases(self) -> list[FilterBranch]:
        cases = self.params.tests
        if not cases:
            raise ConfigError("au moins une condition est nécessaire")
        seen: set[str] = set()
        for c in cases:
            if not _PORT.match(c.output):
                raise ConfigError(f"nom de sortie invalide : « {c.output} » (lettres, chiffres et _)")
            if c.output in _RESERVED:
                raise ConfigError(f"nom de sortie réservé : {c.output}")
            if not c.condition.strip():
                raise ConfigError(f"la sortie {c.output} n'a pas de condition")
            if c.output in seen and self.params.mode == "first":
                raise ConfigError(f"sortie en double : {c.output}")
            seen.add(c.output)
        return cases

    def output_schema(self, inputs: dict[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._cases()
        schema = inputs.get("input")
        return {port: schema for port in self.ports(self.params)[1] if port != REJECTED}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        cases = self._cases()
        n = batch.num_rows
        errors: dict[int, str] = {}
        masks = []
        for case in cases:
            mask, err = safe_mask(batch, case.condition)
            masks.append(mask.to_numpy(zero_copy_only=False).astype(bool))
            for i, message in err.items():
                errors.setdefault(i, f"{case.condition} : {message}")
        bad = np.zeros(n, dtype=bool)
        if errors:
            bad[list(errors)] = True
        taken = np.zeros(n, dtype=bool)
        parts: dict[str, list[pa.Table]] = {}
        for case, mask in zip(cases, masks, strict=True):
            take = mask & ~bad & (~taken if self.params.mode == "first" else True)
            taken |= take
            if take.any():
                parts.setdefault(case.output, []).append(batch.filter(pa.array(take)))
        out: dict[str, pa.Table] = {port: pa.concat_tables(tables) for port, tables in parts.items()}
        failed = ~taken & ~bad
        if failed.any():
            out[FAILED] = batch.filter(pa.array(failed))
        if errors:
            idx = sorted(errors)
            out[REJECTED] = reject(batch.take(pa.array(idx)), pa.array([errors[i] for i in idx], pa.string()))
        return out

    def used_attributes(self) -> list[str]:
        return []
