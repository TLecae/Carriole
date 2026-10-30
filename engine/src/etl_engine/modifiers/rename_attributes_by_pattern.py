"""RenameAttributesByPattern : renomme d'un coup tous les attributs (ou une sélection) selon une règle."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params, missing_attributes


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


class RenameAttributesByPattern(Modifier):
    name = "RenameAttributesByPattern"
    label = "Renommer en masse"
    category = "Attributs"
    description = "Renomme tous les attributs (ou une sélection) : préfixe, suffixe, remplacement, casse, accents."
    help = (
        "Les opérations s'appliquent dans cet ordre au nom de chaque attribut : remplacement (texte ou expression "
        "régulière), retrait des accents, espaces et caractères spéciaux remplacés par _, casse, puis préfixe et suffixe. "
        "Sans sélection, tous les attributs sont renommés (la géométrie jamais). Deux attributs qui auraient le même "
        "nouveau nom, ou un nom déjà pris par un attribut non renommé, sont refusés. Utile avant une écriture "
        "Shapefile (noms de 10 caractères) ou pour normaliser un export."
    )
    rejected = False

    class Params(Params):
        attributes: AttributeList = Field(
            default_factory=list, title="Attributs", description="Vide = tous les attributs (hors géométrie)."
        )
        find: str = Field(
            "", title="Remplacer", description="Texte (ou expression régulière) à remplacer dans les noms."
        )
        replace: str = Field("", title="Par", description="Texte de remplacement (\\1 pour un groupe de l'expression).")
        regex: bool = Field(
            False, title="Expression régulière", description="« Remplacer » est une expression régulière."
        )
        strip_accents: bool = Field(False, title="Retirer les accents", description="é devient e, ç devient c…")
        clean: bool = Field(
            False,
            title="Nettoyer",
            description="Remplace espaces et caractères spéciaux par _ (« Code INSEE » devient Code_INSEE).",
        )
        case: Literal["none", "lower", "upper", "snake"] = Field(
            "none",
            title="Casse",
            description="none : inchangée ; lower / upper ; snake : minuscules_avec_tirets_bas (CodeInsee devient code_insee).",
        )
        prefix: str = Field("", title="Préfixe", description="Ajouté devant chaque nom.")
        suffix: str = Field("", title="Suffixe", description="Ajouté derrière chaque nom.")
        max_length: int = Field(
            0,
            ge=0,
            le=255,
            title="Longueur maximale",
            description="Coupe les noms trop longs (0 = pas de limite ; 10 pour un Shapefile).",
        )

    def _new_name(self, name: str) -> str:
        p = self.params
        out = name
        if p.find:
            try:
                out = re.sub(p.find, p.replace, out) if p.regex else out.replace(p.find, p.replace)
            except re.error as exc:
                raise ConfigError(f"expression régulière invalide : {exc}") from exc
        if p.strip_accents:
            out = _plain(out)
        if p.clean:
            out = re.sub(r"[^\w]+", "_", out).strip("_")
        if p.case == "snake":
            out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", out).lower()
        elif p.case == "lower":
            out = out.lower()
        elif p.case == "upper":
            out = out.upper()
        out = f"{p.prefix}{out}{p.suffix}"
        return out[: p.max_length] if p.max_length else out

    def _mapping(self, names: list[str]) -> dict[str, str]:
        selected = set(self.params.attributes) if self.params.attributes else set(names)
        missing = [a for a in self.params.attributes if a not in names]
        if missing:
            raise missing_attributes(missing)
        mapping = {n: self._new_name(n) if n in selected and n != GEOM else n for n in names}
        if any(not v for v in mapping.values()):
            raise ConfigError("un nom d'attribut deviendrait vide")
        final = list(mapping.values())
        dup = sorted({v for v in final if final.count(v) > 1})
        if dup:
            raise ConfigError(f"noms en double après renommage : {dup}")
        return mapping

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        mapping = self._mapping(schema.names)
        return {"output": pa.schema([f.with_name(mapping[f.name]) for f in schema], metadata=schema.metadata)}

    def sql(self, relations, schemas):
        schema = schemas.get("input")
        if schema is None:
            return None
        mapping = self._mapping(schema.names)
        columns = ", ".join(f"{quote(n)} AS {quote(mapping[n])}" for n in schema.names)
        return {"output": f"SELECT {columns} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        mapping = self._mapping(batch.column_names)
        out = batch.rename_columns([mapping[c] for c in batch.column_names])
        return {"output": out.replace_schema_metadata(batch.schema.metadata)}
