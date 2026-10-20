"""JoinByField : jointure attributaire entre deux flux."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect, join_result, match_pairs, quote
from etl_engine.data import GEOM
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params


class JoinByField(Modifier):
    name = "JoinByField"
    label = "Jointure attributaire"
    category = "Jointures"
    description = "Ajoute aux entités de « base » les attributs des entités de « join » qui ont la même clé."
    help = (
        "Les entités de « base » avec correspondance sortent sur « Joined » ; les autres sur « UnjoinedBase ». "
        "Les entités de « join » qui ne correspondent à aucune entité de « base » sortent sur « UnjoinedJoin ». "
        "Une clé nulle ne "
        "correspond jamais. Si les clés n'ont pas le même type (entier et texte), la comparaison se fait en texte. "
        "La géométrie est celle de « base »."
    )
    mode = "blocking"
    inputs = ("base", "join")
    outputs = ("Joined", "UnjoinedBase", "UnjoinedJoin")
    rejected = False

    class Params(Params):
        base_keys: AttributeList = Field(
            [],
            title="Clés de « base »",
            description=(
                "Attributs de « base » qui font le lien avec « join », dans le même ordre que les clés de « join »."
            ),
        )
        join_keys: AttributeList = Field([], title="Clés de « join »", description="Dans le même ordre que « base ».")
        attributes: AttributeList = Field(
            [], title="Attributs à ajouter", description="Vide = tous ceux de « join » hors clés."
        )
        prefix: str = Field("", title="Préfixe des attributs ajoutés", description="Évite les conflits de noms.")
        conflict: Literal["other", "base"] = Field(
            "other",
            title="Si le nom existe déjà",
            description="other : la valeur de « join » ; base : on garde l'existante.",
        )
        on_multiple: Literal["all", "first", "error"] = Field(
            "all", title="Plusieurs correspondances", description="all : une entité par correspondance ; first ; error."
        )
        keep_unjoined: bool = Field(
            False, title="Garder les non-appariées dans Joined", description="Jointure « gauche » : attributs vides."
        )

    def _check(self) -> None:
        p = self.params
        if not p.base_keys or len(p.base_keys) != len(p.join_keys):
            raise ConfigError("indiquer autant de clés de « base » que de clés de « join » (au moins une)")

    def _attributes(self, other: pa.Schema) -> list[str]:
        p = self.params
        if p.attributes:
            absent = [a for a in p.attributes if a not in other.names]
            if absent:
                raise ConfigError(f"attributs absents de « join » : {absent}")
            return list(p.attributes)
        return [n for n in other.names if n not in p.join_keys and n != GEOM]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        base, other = inputs.get("base"), inputs.get("join")
        if base is None or other is None:
            return {"Joined": None, "UnjoinedBase": base, "UnjoinedJoin": other}
        empty_pairs = pa.table({"bi": pa.array([], pa.int64()), "ji": pa.array([], pa.int64())})
        joined, *_ = join_result(
            base.empty_table(), other.empty_table(), empty_pairs, self._attributes(other), self.params.prefix,
            self.params.conflict,
        )  # fmt: skip
        return {"Joined": joined.schema, "UnjoinedBase": base, "UnjoinedJoin": other}

    def sql(self, relations, schemas):
        """Même jointure en SQL (DuckDB, avec débordement sur disque pour les gros volumes)."""
        self._check()
        p = self.params
        base, other = schemas.get("base"), schemas.get("join")
        if base is None or other is None or p.on_multiple == "error":
            return None  # l'erreur « plusieurs correspondances » est levée par la voie Python
        for side, schema, keys in (("base", base, p.base_keys), ("join", other, p.join_keys)):
            absent = [k for k in keys if k not in schema.names]
            if absent:
                raise ConfigError(f"clés absentes de « {side} » : {absent}")
        attributes = self._attributes(other)
        b, j = relations["base"], relations["join"]
        conditions = []
        for left, right in zip(p.base_keys, p.join_keys, strict=True):
            same = base.field(left).type == other.field(right).type
            lq, rq = f"b.{quote(left)}", f"j.{quote(right)}"
            conditions.append(f"{lq} = {rq}" if same else f"CAST({lq} AS VARCHAR) = CAST({rq} AS VARCHAR)")
        on = " AND ".join(conditions)
        columns = []  # colonnes de base (remplacées en cas de conflit), puis colonnes ajoutées
        replaced = {}
        extra = []
        for attr in attributes:
            target = p.prefix + attr
            if target == GEOM:
                continue
            if target in base.names:
                if p.conflict == "other":
                    # jointure « gauche » : sans correspondance, l'entité garde sa propre valeur
                    keep = f"CASE WHEN j.__j IS NULL THEN b.{quote(target)} ELSE j.{quote(attr)} END"
                    replaced[target] = keep if p.keep_unjoined else f"j.{quote(attr)}"
            else:
                extra.append(f"j.{quote(attr)} AS {quote(target)}")
        for name in base.names:
            columns.append(f"{replaced[name]} AS {quote(name)}" if name in replaced else f"b.{quote(name)}")
        select = ", ".join(columns + extra)
        nb = f"SELECT row_number() OVER () AS __b, * FROM {b}"
        nj = f"SELECT row_number() OVER () AS __j, * FROM {j}"
        first = "QUALIFY row_number() OVER (PARTITION BY b.__b ORDER BY j.__j) = 1" if p.on_multiple == "first" else ""
        kind = "LEFT JOIN" if p.keep_unjoined else "JOIN"
        joined = f"SELECT {select} FROM ({nb}) b {kind} ({nj}) j ON {on} {first} ORDER BY (j.__j IS NULL), b.__b, j.__j"
        base_list = ", ".join(f"b.{quote(n)}" for n in base.names)
        join_list = ", ".join(f"j.{quote(n)}" for n in other.names)
        out = {"Joined": joined}
        missing = "FALSE" if p.keep_unjoined else f"NOT EXISTS (SELECT 1 FROM {j} j WHERE {on})"
        out["UnjoinedBase"] = f"SELECT {base_list} FROM ({nb}) b WHERE {missing} ORDER BY b.__b"
        out["UnjoinedJoin"] = (
            f"SELECT {join_list} FROM ({nj}) j WHERE NOT EXISTS (SELECT 1 FROM {b} b WHERE {on}) ORDER BY j.__j"
        )
        return out

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        base, other = collect(inputs.get("base", ())), collect(inputs.get("join", ()))
        if base is None:
            return
        if other is None:
            emit("UnjoinedBase", base)
            return
        for side, table, keys in (("base", base, p.base_keys), ("join", other, p.join_keys)):
            absent = [k for k in keys if k not in table.column_names]
            if absent:
                raise ConfigError(f"clés absentes de « {side} » : {absent}")
        pairs = match_pairs(base, other, list(p.base_keys), list(p.join_keys))
        joined, unmatched, _, _ = join_result(
            base, other, pairs, self._attributes(other.schema), p.prefix, p.conflict, "base", p.on_multiple
        )
        if p.keep_unjoined and len(unmatched):
            rest = base.take(pa.array(unmatched, pa.int64()))
            for name in joined.column_names:
                if name not in rest.column_names:
                    rest = rest.append_column(name, pa.nulls(rest.num_rows, joined.schema.field(name).type))
            joined = pa.concat_tables([joined, rest.select(joined.column_names)], promote_options="default")
            unmatched = np.array([], dtype=np.int64)
        if joined.num_rows:
            emit("Joined", joined)
        if len(unmatched):
            emit("UnjoinedBase", base.take(pa.array(unmatched, pa.int64())))
        unused = np.setdiff1d(np.arange(other.num_rows), pairs.column("ji").to_numpy())  # jamais appariées
        if len(unused):
            emit("UnjoinedJoin", other.take(pa.array(unused, pa.int64())))
        self.ctx.info(f"{joined.num_rows} jointure(s), {len(unmatched)} entité(s) de base sans correspondance")
