"""AddAttributes : crée ou remplace des attributs à partir d'expressions."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.expr import safe_evaluate
from etl_engine.sdk import REJECTED, ConfigError, Expression, Modifier, Params, reject

SQL_TYPES = {
    "text": "VARCHAR",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}

AttrType = Literal["auto", "text", "integer", "float", "boolean", "date", "datetime"]


def rows_to_reject(batch: pa.Table, original_index: np.ndarray, errors: dict[int, str]) -> pa.Table | None:
    """Lignes d'origine (par indice global) à envoyer sur `Rejected`, avec leur motif."""
    if not errors:
        return None
    local = sorted(errors)
    taken = batch.take(pa.array(original_index[local]))
    return reject(taken, pa.array([errors[i] for i in local], pa.string()))


def apply_expressions(batch: pa.Table, specs: list[tuple[str, str, str]]) -> tuple[pa.Table, pa.Table | None]:
    """Applique (nom, expression, type) en séquence : chaque expression voit les précédentes.

    Renvoie (table enrichie des lignes valides, lignes rejetées d'origine avec motif).
    """
    table = batch
    alive = np.arange(batch.num_rows)
    rejected: list[pa.Table] = []
    for name, expression, kind in specs:
        sql = f"TRY_CAST(({expression}) AS {SQL_TYPES[kind]})" if kind != "auto" else expression
        result, errors = safe_evaluate(table, {name: sql})
        column = result.column(name)
        if kind != "auto":
            # TRY_CAST rend NULL quand la conversion est impossible : on distingue de « résultat vide »
            raw, _ = safe_evaluate(table, {"raw": expression})
            failed = pc.and_(pc.is_null(column), pc.is_valid(raw.column("raw")))
            for i in np.flatnonzero(failed.to_numpy(zero_copy_only=False)):
                errors.setdefault(int(i), f"conversion impossible de « {name} » en {kind}")
        if errors:
            part = rows_to_reject(batch, alive, {i: f"{name} : {m}" for i, m in errors.items()})
            if part is not None:
                rejected.append(part)
            keep = np.ones(table.num_rows, dtype=bool)
            keep[list(errors)] = False
            table = table.filter(pa.array(keep))
            column = column.filter(pa.array(keep))
            alive = alive[keep]
        if name in table.column_names:
            table = table.set_column(table.column_names.index(name), name, column)
        else:
            table = table.append_column(name, column)
    out_rejected = pa.concat_tables(rejected) if rejected else None
    return with_crs(table, get_crs(batch)), out_rejected


class AttributeSpec(Params):
    name: str = Field(
        "", title="Nom de l'attribut", description="Nom de l'attribut créé ; s'il existe déjà, sa valeur est remplacée."
    )
    expression: Expression = Field("", title="Expression", description='Expression SQL, ex. "surface" * 2.')
    type: AttrType = Field(
        "auto",
        title="Type",
        description="auto : celui de l'expression. Sinon le résultat est converti ; échec = rejet.",
    )


class AddAttributes(Modifier):
    name = "AddAttributes"
    label = "Créer des attributs"
    category = "Attributs"
    description = "Ajoute ou remplace des attributs calculés par des expressions SQL."
    help = (
        "Chaque attribut est calculé par une expression SQL DuckDB, dans l'ordre : une expression peut utiliser "
        'les attributs créés plus haut. Exemples : "surface" * 2 ; upper("nom") ; '
        "CASE WHEN \"pop\" > 1000 THEN 'ville' ELSE 'village' END ; ST_Area(geometry). "
        "Une entité dont l'expression échoue part sur « Rejected » avec le motif."
    )

    class Params(Params):
        attributes: list[AttributeSpec] = Field(
            [], title="Attributs à créer", description="Calculés dans l'ordre : chacun peut utiliser les précédents."
        )

    def _specs(self) -> list[tuple[str, str, str]]:
        specs = []
        for a in self.params.attributes:
            if not a.name or not a.expression:
                raise ConfigError("chaque attribut demande un nom et une expression")
            specs.append((a.name, a.expression, a.type))
        return specs

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        out, _ = apply_expressions(schema.empty_table(), self._specs())
        return {"output": out.schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        out, rejected = apply_expressions(batch, self._specs())
        result = {"output": out}
        if rejected is not None:
            result[REJECTED] = rejected
        return result
