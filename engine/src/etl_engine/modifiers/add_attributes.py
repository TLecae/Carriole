"""AddAttributes : crée ou remplace des attributs à partir d'expressions."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.expr import guarded, quote, safe_evaluate, sql_base, sql_columns
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


def apply_expressions(
    batch: pa.Table, specs: list[tuple[str, str, str]], on_error: str = "reject"
) -> tuple[pa.Table, pa.Table | None]:
    """Applique (nom, expression, type) en séquence : chaque expression voit les précédentes.

    Renvoie (table enrichie des lignes valides, lignes rejetées d'origine avec motif). `on_error="null"` : une
    expression qui échoue, ou une valeur non convertible, laisse l'attribut vide au lieu de rejeter l'entité.
    """
    table = batch
    alive = np.arange(batch.num_rows)
    rejected: list[pa.Table] = []
    for name, expression, kind in specs:
        if kind == "auto":
            result, errors = safe_evaluate(table, {name: expression})
        else:  # TRY_CAST rend NULL quand la conversion est impossible : on le distingue d'un résultat vide
            converted = {
                name: f"TRY_CAST(({expression}) AS {SQL_TYPES[kind]})",
                "__given": f"({expression}) IS NOT NULL",
            }
            result, errors = safe_evaluate(table, converted)
        column = result.column(name)
        if on_error == "null":  # les lignes en erreur ont déjà une valeur vide
            errors = {}
        elif kind != "auto":
            failed = pc.and_(pc.is_null(column), pc.fill_null(result.column("__given"), False))
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
        description="auto : celui de l'expression. Sinon le résultat est converti (échec : voir « En cas d'échec »).",
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
        "Une entité dont l'expression échoue (ou dont la valeur ne se convertit pas dans le type demandé) part sur "
        "« Rejected » avec le motif ; avec « En cas d'échec : null », l'attribut reste vide."
    )

    class Params(Params):
        attributes: list[AttributeSpec] = Field(
            [], title="Attributs à créer", description="Calculés dans l'ordre : chacun peut utiliser les précédents."
        )
        on_error: Literal["reject", "null"] = Field(
            "reject",
            title="En cas d'échec",
            description="reject : l'entité part sur « Rejected » avec le motif ; null : l'attribut reste vide.",
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
        out, _ = apply_expressions(schema.empty_table(), self._specs(), self.params.on_error)
        return {"output": out.schema}

    def sql(self, relations, schemas):
        schema, out = schemas.get("input"), self.output_schema(dict(schemas)).get("output")
        specs = self._specs()
        names = [name for name, _, _ in specs]
        if schema is None or out is None or GEOM in names or len(set(names)) < len(names):
            return None  # géométrie recalculée, attribut écrit deux fois : la voie par lots
        texts = [expression for _, expression, _ in specs]
        query = sql_base(relations["input"], schema, texts)
        present = set(schema.names)
        for name, expression, kind in specs:
            if self.params.on_error == "null":  # pas d'erreur possible : rien à contrôler ni à rejeter
                value = f"TRY({expression})" if kind == "auto" else f"TRY_CAST(({expression}) AS {SQL_TYPES[kind]})"
            else:
                value = guarded(expression if kind == "auto" else f"CAST(({expression}) AS {SQL_TYPES[kind]})")
            if name in present:
                query = f"SELECT * REPLACE ({value} AS {quote(name)}) FROM ({query})"
            else:
                query = f"SELECT *, {value} AS {quote(name)} FROM ({query})"
                present.add(name)
        return {"output": f"SELECT {sql_columns(out.names, schema, texts)} FROM ({query})"}

    def sql_checked(self) -> list[str]:
        return [a.name for a in self.params.attributes if a.name] if self.params.on_error == "reject" else []

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        out, rejected = apply_expressions(batch, self._specs(), self.params.on_error)
        result = {"output": out}
        if rejected is not None:
            result[REJECTED] = rejected
        return result
