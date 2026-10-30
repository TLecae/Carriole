"""DatabaseQueryPerFeature : exécute un ordre SQL pour chaque entité reçue."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.formats import databases
from etl_engine.sdk import ConfigError, DbConnection, Emit, Modifier, Params, missing_attributes


class DatabaseQueryPerFeature(Modifier):
    name = "DatabaseQueryPerFeature"
    reusable = False  # toujours relancé (voir Modifier.reusable)
    label = "SQL (par entité)"
    category = "Jointures"
    description = "Exécute un ordre SQL pour chaque entité, en y injectant ses attributs ; ajoute le résultat."
    help = (
        "Écrire `{{attribut}}` dans le SQL : la valeur de l'entité est transmise comme **paramètre lié** (jamais "
        "collée dans le texte : pas d'injection SQL). Ex. SELECT nom FROM communes WHERE code = {{insee}}. "
        "« first » ajoute les colonnes de la première ligne du résultat ; « all » produit une entité par ligne. "
        "Une entité sans résultat part sur « NoResult ». Lecture seule sauf « Autoriser l'écriture » (tout est "
        "validé à la fin, annulé en cas d'erreur). Une requête par entité : prévoir de la jointure SQL si le "
        "volume est important."
    )
    outputs = ("output", "NoResult")
    rejected = False
    stateful = True

    class Params(Params):
        connection: DbConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
        sql: str = Field(
            "",
            title="SQL",
            description="Avec {{attribut}} pour les valeurs de l'entité.",
            examples=["SELECT nom FROM communes WHERE code = {{insee}}"],
        )
        rows: Literal["first", "all"] = Field(
            "first",
            title="Lignes du résultat",
            description=(
                "first : les colonnes de la première ligne du résultat sont ajoutées à l'entité ; all : une "
                "entité par ligne du résultat."
            ),
        )
        prefix: str = Field(
            "", title="Préfixe des colonnes ajoutées", description="Ajouté devant le nom des colonnes ajoutées."
        )
        allow_write: bool = Field(
            False,
            title="Autoriser l'écriture",
            description="Décoché : lecture seule (SELECT). Coché : UPDATE, INSERT… autorisés, validés à la fin.",
        )

    def _check(self) -> tuple[str, list[str]]:
        p = self.params
        if not p.connection:
            raise ConfigError("connexion non renseignée")
        if not p.sql.strip():
            raise ConfigError("SQL vide")
        return databases.backend(p.connection).bind_placeholders(p.sql.strip().rstrip(";"))

    def used_attributes(self) -> list[str]:
        from etl_engine.formats.dbapi import PLACEHOLDER

        return list(dict.fromkeys(PLACEHOLDER.findall(self.params.sql)))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        statement, names = self._check()
        schema = inputs.get("input")
        if schema is None or self.params.allow_write:
            return {"output": None, "NoResult": schema}
        try:
            session = databases.backend(self.params.connection).session(self.params.connection, read_only=True)
            try:
                extra = session.describe(statement, names)
            finally:
                session.close()
        except Exception:  # noqa: BLE001 - base injoignable à l'édition : le run le dira
            return {"output": None, "NoResult": schema}
        out = schema
        for field in extra:
            name = self.params.prefix + field.name
            if name not in out.names:
                out = out.append(pa.field(name, field.type))
        return {"output": out, "NoResult": schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        statement, names = self._check()
        p = self.params
        session = databases.backend(p.connection).session(p.connection, read_only=not p.allow_write)
        done = 0
        try:
            for batch in inputs.get("input", ()):
                self.ctx.check_cancel()
                missing = [n for n in names if n not in batch.column_names]
                if missing:
                    raise missing_attributes(missing)
                values = {n: batch.column(n).to_pylist() for n in names}
                out_rows: list[int] = []  # indice de l'entité d'origine pour chaque ligne de sortie
                results: list[pa.Table] = []
                none: list[int] = []
                for i in range(batch.num_rows):
                    table = session.execute(statement, {n: values[n][i] for n in names})
                    if table is None or table.num_rows == 0:
                        none.append(i)
                        continue
                    if p.rows == "first":
                        table = table.slice(0, 1)
                    results.append(table)
                    out_rows += [i] * table.num_rows
                if none:
                    emit("NoResult", batch.take(pa.array(none, pa.int64())))
                if results:
                    base = batch.take(pa.array(out_rows, pa.int64()))
                    extra = pa.concat_tables(results)
                    for j, name in enumerate(extra.column_names):
                        target = p.prefix + name
                        if target in base.column_names:
                            base = base.set_column(base.column_names.index(target), target, extra.column(j))
                        else:
                            base = base.append_column(target, extra.column(j))
                    emit("output", with_crs(base, get_crs(batch)))
                done += batch.num_rows
            if p.allow_write:
                session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
        self.ctx.info(f"{done} entité(s) traitée(s)")
