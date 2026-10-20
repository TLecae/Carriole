"""LookupJoin : complète les entités « input » avec les attributs (et la géométrie) d'une table « lookup »."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect, join_result, match_pairs
from etl_engine.data import GEOM
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params


class LookupJoin(Modifier):
    name = "LookupJoin"
    label = "Compléter par correspondance"
    category = "Jointures"
    description = (
        "Ajoute à chaque entité « input » les attributs (et au choix la géométrie) de sa correspondance « lookup »."
    )
    help = (
        "Même principe que la jointure attributaire, avec en plus le choix de la géométrie résultante : celle de "
        "l'entrée, celle de la correspondance, ou aucune. Les entités complétées sortent sur « Joined », les autres "
        "sur « Unjoined ». Plusieurs correspondances pour une clé : la première est retenue (ou toutes les "
        "combinaisons, au choix)."
    )
    mode = "blocking"
    inputs = ("input", "lookup")
    outputs = ("Joined", "Unjoined", "UnusedLookup")
    rejected = False

    class Params(Params):
        input_keys: AttributeList = Field(
            [],
            title="Clés de l'entrée",
            description="Attributs de l'entrée qui font le lien avec la correspondance, dans le même ordre.",
        )
        lookup_keys: AttributeList = Field([], title="Clés de la correspondance", description="Dans le même ordre.")
        attributes: AttributeList = Field([], title="Attributs ajoutés", description="Vide = tous hors clés.")
        geometry: Literal["input", "lookup", "none"] = Field(
            "input",
            title="Géométrie résultante",
            description="Géométrie des entités complétées : celle de l'entrée, celle de la correspondance, ou aucune.",
        )
        conflict: Literal["lookup", "input"] = Field(
            "lookup", title="Si le nom existe déjà", description="Quelle valeur l'emporte."
        )
        on_multiple: Literal["first", "all"] = Field(
            "first",
            title="Plusieurs correspondances",
            description=(
                "Si plusieurs correspondances ont la même clé — first : on garde la première ; all : toutes les "
                "combinaisons."
            ),
        )

    def _check(self) -> None:
        p = self.params
        if not p.input_keys or len(p.input_keys) != len(p.lookup_keys):
            raise ConfigError("indiquer autant de clés de chaque côté (au moins une)")

    def _attributes(self, lookup: pa.Schema) -> list[str]:
        p = self.params
        if p.attributes:
            absent = [a for a in p.attributes if a not in lookup.names]
            if absent:
                raise ConfigError(f"attributs absents de la correspondance : {absent}")
            return list(p.attributes)
        return [n for n in lookup.names if n not in p.lookup_keys and n != GEOM]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        req, sup = inputs.get("input"), inputs.get("lookup")
        if req is None or sup is None:
            return {"Joined": None, "Unjoined": req, "UnusedLookup": sup}
        empty = pa.table({"bi": pa.array([], pa.int64()), "ji": pa.array([], pa.int64())})
        merged, *_ = join_result(
            req.empty_table(), sup.empty_table(), empty, self._attributes(sup), "", self._conflict(), self._geometry()
        )  # fmt: skip
        return {"Joined": merged.schema, "Unjoined": req, "UnusedLookup": sup}

    def _geometry(self) -> str:
        return {"input": "base", "lookup": "other", "none": "none"}[self.params.geometry]

    def _conflict(self) -> str:
        return "other" if self.params.conflict == "lookup" else "base"

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        req, sup = collect(inputs.get("input", ())), collect(inputs.get("lookup", ()))
        if req is None:
            return
        if sup is None:
            emit("Unjoined", req)
            return
        for side, table, keys in (("de l'entrée", req, p.input_keys), ("de la correspondance", sup, p.lookup_keys)):
            absent = [k for k in keys if k not in table.column_names]
            if absent:
                raise ConfigError(f"clés absentes {side} : {absent}")
        pairs = match_pairs(req, sup, list(p.input_keys), list(p.lookup_keys))
        merged, unmatched, _, ji = join_result(
            req, sup, pairs, self._attributes(sup.schema), "", self._conflict(), self._geometry(), p.on_multiple
        )
        if merged.num_rows:
            emit("Joined", merged)
        if len(unmatched):
            emit("Unjoined", req.take(pa.array(unmatched, pa.int64())))
        unused = np.setdiff1d(np.arange(sup.num_rows), ji)
        if len(unused):
            emit("UnusedLookup", sup.take(pa.array(unused, pa.int64())))
        self.ctx.info(f"{merged.num_rows} entité(s) complétée(s), {len(unmatched)} sans correspondance")
