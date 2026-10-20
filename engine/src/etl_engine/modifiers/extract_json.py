"""ExtractJson : extrait des valeurs d'un attribut JSON, et peut éclater un tableau en entités."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import duck, quote
from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params

SQL_TYPES = {"text": "VARCHAR", "integer": "BIGINT", "float": "DOUBLE", "boolean": "BOOLEAN"}
ARROW_TYPES = {"text": pa.string(), "integer": pa.int64(), "float": pa.float64(), "boolean": pa.bool_()}
Kind = Literal["text", "integer", "float", "boolean"]


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _json_path(path: str) -> str:
    path = path.strip()
    return path if path.startswith("$") else "$." + path.lstrip(".")


class JsonField(Params):
    path: str = Field(
        "", title="Chemin", description="Chemin dans le JSON : a.b, a[0].b ou $.a.b (vide = tout l'élément)."
    )
    name: str = Field("", title="Attribut", description="Nom de l'attribut créé.")
    type: Kind = Field("text", title="Type", description="text, integer, float ou boolean ; non convertible = vide.")


class ExtractJson(Modifier):
    name = "ExtractJson"
    label = "Extraire du JSON"
    category = "Attributs"
    description = (
        "Lit un attribut qui contient du JSON : extrait des valeurs dans de nouveaux attributs, ou éclate un tableau."
    )
    help = (
        "Chaque ligne de « Valeurs à extraire » lit un chemin (a.b, a[0].b, $.a.b) et le range dans un attribut typé. "
        "Avec « Tableau à éclater » (par exemple items ou $.results), le JSON de chaque entité donne **une entité par "
        "élément du tableau** : les chemins sont alors lus dans l'élément (chemin vide = l'élément entier, en texte), et "
        "les autres attributs sont recopiés. Un JSON invalide ou un chemin absent donne une valeur vide. Typique après un "
        "appel HTTP (HttpRequest)."
    )
    rejected = False

    class Params(Params):
        attribute: Attribute = Field("", title="Attribut JSON", description="Attribut texte qui contient le JSON.")
        fields: list[JsonField] = Field(
            default_factory=list, title="Valeurs à extraire", description="Un chemin et l'attribut qui le reçoit."
        )
        explode: str = Field(
            "",
            title="Tableau à éclater",
            description="Chemin d'un tableau : une entité par élément ; vide = une entité par ligne.",
            examples=["$.results"],
        )
        drop_source: bool = Field(
            False, title="Retirer l'attribut JSON", description="Retire l'attribut d'origine du résultat."
        )

    def _check(self) -> None:
        p = self.params
        if not p.attribute:
            raise ConfigError("attribut JSON non renseigné")
        if not p.fields and not p.explode:
            raise ConfigError("rien à extraire : indiquer des valeurs ou un tableau à éclater")
        names = [f.name for f in p.fields]
        if any(not n for n in names):
            raise ConfigError("chaque valeur demande un nom d'attribut")
        if len(set(names)) != len(names):
            raise ConfigError("attribut en double")

    def _query(self, columns: list[str]) -> str:
        p = self.params
        source = f"CASE WHEN json_valid({quote(p.attribute)}) THEN {quote(p.attribute)} END"  # JSON invalide = vide
        keep = [quote(c) for c in columns if not (p.drop_source and c == p.attribute)]
        if p.explode:
            base = f"SELECT {', '.join(keep) or '1 AS _k'}, UNNEST(CAST(json_extract({source}, {_lit(_json_path(p.explode))}) AS JSON[])) AS _elem FROM t"
            holder = "_elem"
        else:
            base = f"SELECT {', '.join(keep) or '1 AS _k'}, {source} AS _elem FROM t"
            holder = "_elem"
        extracted = []
        for f in p.fields:
            if f.path.strip():
                value = f"json_extract_string({holder}, {_lit(_json_path(f.path))})"
            else:
                value = f"CAST({holder} AS VARCHAR)"
            extracted.append(f"TRY_CAST({value} AS {SQL_TYPES[f.type]}) AS {quote(f.name)}")
        outer = [quote(c) for c in columns if not (p.drop_source and c == p.attribute)]
        # les attributs extraits remplacent ceux du même nom
        outer = [c for c in outer if c.strip('"') not in {f.name for f in p.fields}]
        return f"SELECT {', '.join([*outer, *extracted])} FROM ({base})"

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        return {"output": self._compute(schema.empty_table()).schema}

    def _compute(self, batch: pa.Table) -> pa.Table:
        source = batch.column(self.params.attribute)
        if not pa.types.is_string(source.type):
            batch = batch.set_column(
                batch.column_names.index(self.params.attribute), self.params.attribute, source.cast(pa.string())
            )
        out = duck(self._query(batch.column_names), t=batch)
        for f in self.params.fields:  # types Arrow stables, y compris pour une table vide
            out = out.set_column(out.column_names.index(f.name), f.name, out.column(f.name).cast(ARROW_TYPES[f.type]))
        return with_crs(out, get_crs(batch))

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        if self.params.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        return {"output": self._compute(batch)}
