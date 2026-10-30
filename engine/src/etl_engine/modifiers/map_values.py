"""MapValues : remplace des valeurs selon une table de correspondance."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.expr import quote
from etl_engine.sdk import REJECTED, Attribute, ConfigError, Modifier, Params, reject


class ValueMapping(Params):
    source: str = Field(
        "", title="Valeur d'origine", description="Valeur à remplacer, comparée en texte (1 et « 1 » sont égaux)."
    )
    target: str = Field("", title="Remplacée par", description="Valeur écrite à la place.")


class MapValues(Modifier):
    name = "MapValues"
    label = "Table de correspondance"
    category = "Attributs"
    description = "Remplace les valeurs d'un attribut selon une table « valeur d'origine → valeur »."
    help = (
        "La comparaison se fait sur le texte de la valeur (1 et « 1 » sont égaux). Pour les valeurs absentes de "
        "la table : « keep » les conserve, « default » met la valeur par défaut, « null » les vide, « reject » "
        "envoie l'entité sur « Rejected ». Le résultat est du texte."
    )
    rejected = True

    class Params(Params):
        attribute: Attribute = Field(
            "", title="Attribut à traduire", description="Attribut dont les valeurs sont traduites."
        )
        destination: str = Field("", title="Attribut résultat", description="Vide = remplace l'attribut d'origine.")
        mappings: list[ValueMapping] = Field(
            [], title="Correspondances", description="Une ligne par valeur à traduire. Le résultat est du texte."
        )
        unmatched: Literal["keep", "default", "null", "reject"] = Field(
            "keep",
            title="Valeur absente de la table",
            description=(
                "Pour une valeur absente de la table — keep : on la garde ; default : on met la valeur par défaut"
                " ; null : on la vide ; reject : l'entité part sur « Rejected »."
            ),
        )
        default: str = Field(
            "", title="Valeur par défaut", description="Utilisée quand « Valeur absente de la table » vaut default."
        )
        ignore_case: bool = Field(
            False, title="Ignorer la casse", description="Ne distingue pas majuscules et minuscules."
        )

    def _target(self) -> str:
        if not self.params.attribute:
            raise ConfigError("attribut à traduire non renseigné")
        return self.params.destination or self.params.attribute

    def output_schema(self, inputs):
        schema = inputs.get("input")
        target = self._target()
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        field = pa.field(target, pa.string())
        if target in schema.names:
            return {"output": schema.set(schema.get_field_index(target), field)}
        return {"output": schema.append(field)}

    def sql(self, relations, schemas):
        """Même correspondance par DuckDB, pour un attribut texte (le texte d'un nombre diffère entre Arrow et DuckDB) ;
        « reject » reste à la voie par lots (le port Rejected)."""
        p = self.params
        target = self._target()
        schema = schemas.get("input")
        if schema is None or p.unmatched == "reject" or not pa.types.is_string(schema.field(p.attribute).type):
            return None
        key = f"lower({quote(p.attribute)})" if p.ignore_case else quote(p.attribute)
        table: dict[str, str] = {}
        for m in p.mappings:  # une valeur donnée deux fois : la dernière l'emporte, comme la voie par lots
            table[m.source.lower() if p.ignore_case else m.source] = m.target
        other = {"keep": quote(p.attribute), "default": _literal(p.default), "null": "NULL"}[p.unmatched]
        cases = " ".join(f"WHEN {_literal(k)} THEN {_literal(v)}" for k, v in table.items())
        expr = f"CASE {key} {cases} ELSE {other} END" if table else other
        expr = f"CAST({expr} AS VARCHAR)"
        if target in schema.names:
            columns = ", ".join(f"{expr} AS {quote(n)}" if n == target else quote(n) for n in schema.names)
        else:
            columns = f"*, {expr} AS {quote(target)}"
        return {"output": f"SELECT {columns} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        target = self._target()
        if p.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.attribute}")
        texts = batch.column(p.attribute).cast(pa.string())
        table = {m.source: m.target for m in p.mappings}  # une valeur donnée deux fois : la dernière l'emporte
        sources, targets = pa.array(list(table), pa.string()), pa.array(list(table.values()), pa.string())
        if p.ignore_case:
            texts_key, sources = pc.utf8_lower(texts), pc.utf8_lower(sources)
            last = {v: i for i, v in enumerate(sources.to_pylist())}  # comme le dict : la dernière l'emporte
            sources = pa.array(list(last), pa.string())
            targets = targets.take(pa.array(list(last.values())))
        else:
            texts_key = texts
        found = pc.index_in(texts_key, value_set=sources)
        mapped = pc.take(targets, found)
        matched = pc.is_valid(found)
        if p.unmatched == "keep":
            column = pc.if_else(matched, mapped, texts)
        elif p.unmatched == "default":
            column = pc.if_else(matched, mapped, pa.scalar(p.default, pa.string()))
        else:
            column = mapped
        column = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
        if target in batch.column_names:
            out = batch.set_column(batch.column_names.index(target), target, column)
        else:
            out = batch.append_column(target, column)
        out = with_crs(out, get_crs(batch))
        if p.unmatched != "reject" or pc.all(matched).as_py() is not False:
            return {"output": out}
        return {
            "output": out.filter(matched),
            REJECTED: reject(batch.filter(pc.invert(matched)), f"valeur de « {p.attribute} » absente de la table"),
        }


def _literal(text: str) -> str:
    """Littéral texte SQL."""
    return "'" + text.replace("'", "''") + "'"
