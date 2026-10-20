"""Modificateur de test : laisse passer les entités après une pause. Sert aux tests d'intégration des runs
(arrêt, kill et annulation du worker en plein run) ; n'a pas sa place dans un plugins/ de production."""

from __future__ import annotations

import time

import pyarrow as pa
from etl_engine.sdk import Modifier, Params


class SlowPass(Modifier):
    name = "SlowPass"
    label = "Pause (test)"
    category = "Tests"
    description = "Attend N secondes avant de laisser passer chaque lot (tests d'intégration)."
    inputs = ("input",)
    outputs = ("output",)

    class Params(Params):
        seconds: float = 20.0

    def output_schema(self, inputs: dict[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        return {"output": inputs.get("input")}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        end = time.monotonic() + self.params.seconds
        while time.monotonic() < end:
            self.ctx.check_cancel()
            time.sleep(0.2)
        return {"output": batch}
