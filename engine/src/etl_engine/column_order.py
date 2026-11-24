"""Ordre des colonnes : celles listées d'abord, dans cet ordre ; les autres à la suite, dans leur ordre d'arrivée ; la
géométrie en dernier. Partagé par ReorderAttributes et le paramètre « Ordre des colonnes » des écrivains."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.sdk import missing_attributes

Others = Literal["after", "before"]


def order_field() -> Any:
    """Paramètre « Ordre des colonnes » d'un écrivain (vide : l'ordre reçu)."""
    return Field(
        default_factory=list,
        title="Ordre des colonnes",
        description="Colonnes écrites d'abord, dans cet ordre ; les autres suivent dans l'ordre reçu.",
        json_schema_extra={"x-etl-widget": "attribute-order"},
    )


def ordered(names: Sequence[str], first: Sequence[str], others: Others = "after") -> list[str]:
    """Noms `names` réordonnés : `first` (sans doublon) puis les autres (ou les autres d'abord : `others="before"`),
    la géométrie en dernier. Un nom de `first` absent de `names` est une erreur de configuration."""
    if missing := [n for n in first if n not in names]:
        raise missing_attributes(missing)
    head = [n for n in dict.fromkeys(first) if n != GEOM]
    rest = [n for n in names if n not in head and n != GEOM]
    return [*(head + rest if others == "after" else rest + head), *([GEOM] if GEOM in names else [])]


def reorder_schema(schema: pa.Schema, first: Sequence[str], others: Others = "after") -> pa.Schema:
    if not first:
        return schema
    names = ordered(schema.names, first, others)
    if names == schema.names:
        return schema
    return pa.schema([schema.field(n) for n in names], metadata=schema.metadata)


def reorder(table: pa.Table, first: Sequence[str], others: Others = "after") -> pa.Table:
    """`table` avec ses colonnes dans l'ordre voulu (sans copie des données)."""
    if not first:
        return table
    names = ordered(table.column_names, first, others)
    return table if names == table.column_names else table.select(names)
