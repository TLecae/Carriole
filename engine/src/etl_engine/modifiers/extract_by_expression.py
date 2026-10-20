"""ExtractByExpression : sépare les entités qui passent une condition de celles qui échouent."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.expr import safe_mask
from etl_engine.sdk import REJECTED, ConfigError, Expression, Modifier, Params, reject


class ExtractByExpression(Modifier):
    name = "ExtractByExpression"
    label = "Filtrer par expression"
    category = "Filtres"
    description = "Envoie les entités sur « Passed » si elles vérifient les conditions, sinon sur « Failed »."
    help = (
        'Chaque condition est une expression SQL booléenne : "pop" >= 1000 ; "nom" LIKE \'Saint%\' ; '
        "\"code\" IN ('31', '09') ; ST_Area(geometry) > 500. Une condition nulle compte comme fausse. "
        "Une entité dont l'évaluation échoue part sur « Rejected » avec le motif."
    )
    outputs = ("Passed", "Failed")

    class Params(Params):
        conditions: list[Expression] = Field(
            [], title="Conditions", description='Expressions SQL booléennes, ex. "pop" >= 1000.'
        )
        combine: Literal["AND", "OR"] = Field(
            "AND", title="Combiner par", description="AND : toutes les conditions ; OR : au moins une."
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        self._check()
        return {"Passed": schema, "Failed": schema}

    def _check(self) -> list[str]:
        tests = [c for c in self.params.conditions if c.strip()]
        if not tests:
            raise ConfigError("au moins une condition est nécessaire")
        return tests

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        tests = self._check()
        n = batch.num_rows
        errors: dict[int, str] = {}
        masks = []
        for t in tests:
            mask, err = safe_mask(batch, t)
            masks.append(mask)
            for i, m in err.items():
                errors.setdefault(i, f"{t} : {m}")
        combined = masks[0]
        for m in masks[1:]:
            combined = pc.and_(combined, m) if self.params.combine == "AND" else pc.or_(combined, m)
        passed = combined.to_numpy(zero_copy_only=False).astype(bool)
        bad = np.zeros(n, dtype=bool)
        if errors:
            bad[list(errors)] = True
        out: dict[str, pa.Table] = {
            "Passed": batch.filter(pa.array(passed & ~bad)),
            "Failed": batch.filter(pa.array(~passed & ~bad)),
        }
        if errors:
            idx = sorted(errors)
            out[REJECTED] = reject(batch.take(pa.array(idx)), pa.array([errors[i] for i in idx], pa.string()))
        return out
