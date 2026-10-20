"""ConvertDate : lit des dates écrites en texte et les réécrit dans un autre format ou type."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.blocking import quote
from etl_engine.data import get_crs, with_crs
from etl_engine.expr import safe_evaluate
from etl_engine.sdk import REJECTED, Attribute, ConfigError, Modifier, Params, reject

Target = Literal["date", "datetime", "text", "epoch"]


class ConvertDate(Modifier):
    name = "ConvertDate"
    label = "Convertir une date"
    category = "Attributs"
    description = "Lit une date écrite en texte (ou un horodatage) et la convertit : date, date-heure, texte formaté, secondes Unix."
    help = (
        "« Format de lecture » est un format strptime (%d/%m/%Y, %Y%m%d, %Y-%m-%d %H:%M:%S…) ; vide = lecture automatique "
        "des formats ISO (2026-10-11, 2026-10-11T08:30:00). Un attribut déjà de type date ou date-heure n'a pas besoin de "
        "format. Résultat : date, date-heure, texte (avec « Format d'écriture », ex. %d/%m/%Y) ou epoch (secondes depuis "
        "1970, en UTC). Une valeur illisible envoie l'entité sur « Rejected », ou devient vide si « Valeur illisible » vaut "
        "null. Codes : %Y année, %m mois, %d jour, %H heure, %M minute, %S seconde, %B nom du mois, %A nom du jour."
    )

    class Params(Params):
        attribute: Attribute = Field("", title="Attribut", description="Attribut qui contient la date.")
        from_format: str = Field(
            "",
            title="Format de lecture",
            description="Format strptime du texte, ex. %d/%m/%Y ; vide = ISO automatique.",
            examples=["%d/%m/%Y"],
        )
        to: Target = Field("date", title="Résultat", description="date, datetime, text ou epoch (secondes Unix, UTC).")
        to_format: str = Field(
            "%Y-%m-%d",
            title="Format d'écriture",
            description="Pour « text » : format strftime, ex. %d/%m/%Y.",
            examples=["%d/%m/%Y"],
        )
        destination: str = Field("", title="Attribut résultat", description="Vide = remplace l'attribut d'origine.")
        on_error: Literal["reject", "null"] = Field(
            "reject",
            title="Valeur illisible",
            description="reject : l'entité part sur « Rejected » ; null : la valeur devient vide.",
        )

    def _target(self) -> str:
        if not self.params.attribute:
            raise ConfigError("attribut non renseigné")
        return self.params.destination or self.params.attribute

    def _out_type(self) -> pa.DataType:
        return {"date": pa.date32(), "datetime": pa.timestamp("us"), "text": pa.string(), "epoch": pa.float64()}[
            self.params.to
        ]

    def output_schema(self, inputs):
        target = self._target()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        field = pa.field(target, self._out_type())
        return {
            "output": schema.set(schema.get_field_index(target), field)
            if target in schema.names
            else schema.append(field)
        }

    def _expression(self, source: pa.DataType) -> str:
        p = self.params
        col = quote(p.attribute)
        if pa.types.is_string(source) or pa.types.is_large_string(source):
            parsed = f"TRY_STRPTIME({col}, {_lit(p.from_format)})" if p.from_format else f"TRY_CAST({col} AS TIMESTAMP)"
        else:
            parsed = f"TRY_CAST({col} AS TIMESTAMP)"
        match p.to:
            case "date":
                return f"CAST({parsed} AS DATE)"
            case "datetime":
                return parsed
            case "text":
                return f"STRFTIME({parsed}, {_lit(p.to_format or '%Y-%m-%d')})"
            case _:
                return f"EPOCH({parsed})"

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        target = self._target()
        if p.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.attribute}")
        source = batch.column(p.attribute)
        result, errors = safe_evaluate(batch, {"_date": self._expression(source.type)})
        column = result.column("_date").cast(self._out_type())
        failed = pc.and_(pc.is_null(column), pc.is_valid(source)).to_numpy(zero_copy_only=False).astype(bool)
        if p.on_error == "reject":
            for i in np.flatnonzero(failed):
                errors.setdefault(int(i), f"date illisible dans « {p.attribute} »")
        table, rejected = batch, None
        if errors:
            idx = sorted(errors)
            rejected = reject(batch.take(pa.array(idx)), pa.array([errors[i] for i in idx], pa.string()))
            keep = np.ones(batch.num_rows, dtype=bool)
            keep[idx] = False
            table = batch.filter(pa.array(keep))
            column = column.filter(pa.array(keep))
        table = (
            table.set_column(table.column_names.index(target), target, column)
            if target in table.column_names
            else table.append_column(target, column)
        )
        out = {"output": with_crs(table, get_crs(batch))}
        if rejected is not None:
            out[REJECTED] = rejected
        return out


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"
