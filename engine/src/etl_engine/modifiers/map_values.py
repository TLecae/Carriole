"""MapValues : remplace des valeurs selon une table de correspondance."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


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
        source: Attribute = Field(
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
        if not self.params.source:
            raise ConfigError("attribut à traduire non renseigné")
        return self.params.destination or self.params.source

    def output_schema(self, inputs):
        schema = inputs.get("input")
        target = self._target()
        if schema is None:
            return {"output": None}
        field = pa.field(target, pa.string())
        if target in schema.names:
            return {"output": schema.set(schema.get_field_index(target), field)}
        return {"output": schema.append(field)}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        from etl_engine.sdk import REJECTED, reject

        p = self.params
        target = self._target()
        if p.source not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.source}")

        def key(v: str) -> str:
            return v.casefold() if p.ignore_case else v

        table = {key(m.source): m.target for m in p.mappings}
        values, rejected = [], []
        for i, v in enumerate(batch.column(p.source).to_pylist()):
            text = None if v is None else str(v)
            if text is not None and key(text) in table:
                values.append(table[key(text)])
            elif p.unmatched == "keep":
                values.append(text)
            elif p.unmatched == "default":
                values.append(p.default)
            elif p.unmatched == "null":
                values.append(None)
            else:
                values.append(None)
                rejected.append(i)
        column = pa.array(values, pa.string())
        if target in batch.column_names:
            out = batch.set_column(batch.column_names.index(target), target, column)
        else:
            out = batch.append_column(target, column)
        out = with_crs(out, get_crs(batch))
        if not rejected:
            return {"output": out}
        keep = pa.array([i not in set(rejected) for i in range(batch.num_rows)])
        return {
            "output": out.filter(keep),
            REJECTED: reject(batch.take(pa.array(rejected)), f"valeur de « {p.source} » absente de la table"),
        }
