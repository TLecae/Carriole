"""RasterCalculator : algèbre de bandes (NDVI, masques, reclassements) par expressions, sur une ou plusieurs entrées."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping
from typing import Any, Literal

import numexpr
import numpy as np
import pyarrow as pa
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.querier import IDENT
from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Emit, Expression, Modifier, Params

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DTYPES = Literal["float32", "float64", "int16", "int32", "uint8", "uint16"]


class Calculation(Params):
    name: str = Field("", title="Nom de la bande", description="Nom de la bande résultat (lettres, chiffres et _).")
    expression: Expression = Field(
        "", title="Expression", description="Ex. (nir - red) / (nir + red)  ou  where(b1 > 100, 1, 0)."
    )


def band_variables(port: str, desc: dict[str, Any], first: bool, multi: bool) -> dict[str, int]:
    """Les noms utilisables dans une expression pour les bandes d'une entrée -> numéro de bande : b1, b2… et le nom de
    chaque bande ; avec plusieurs entrées, ils sont préfixés par le nom du port (`a_b1`, `a_red`), sauf pour la première
    entrée qui garde aussi les noms nus."""
    out: dict[str, int] = {}
    for index in range(1, desc["count"] + 1):
        keys = [f"b{index}"]
        name = desc["band_names"][index - 1] if index <= len(desc["band_names"]) else ""
        if name and name.isidentifier() and not keyword.iskeyword(name):
            keys.append(name)
        for key in keys:
            if first or not multi:
                out.setdefault(key, index)
            if multi:
                out[f"{port}_{key}"] = index
    return out


class RasterCalculator(Modifier):
    name = "RasterCalculator"
    label = "Calculatrice de rasters"
    category = "Rasters"
    description = (
        "Calcule de nouvelles bandes par expressions sur les bandes d'un ou plusieurs rasters (NDVI, masques, seuils)."
    )
    help = (
        "Chaque ligne crée une bande du raster résultat. Dans l'expression, les bandes s'appellent b1, b2… ou par leur "
        "nom (red, nir…) : (nir - red) / (nir + red). Opérateurs + - * / ** % ; comparaisons < <= == != >= > ; logiques & | ~ ; "
        "fonctions where(condition, si_vrai, si_faux), abs, sqrt, exp, log, log10, sin, cos, tan, arctan2, minimum, maximum… "
        "(calcul vectorisé et multithread, sans exécution de code). Un pixel sans donnée dans une bande utilisée reste sans "
        "donnée dans le résultat. Plusieurs entrées (liste « Entrées ») : celles qui suivent la première sont rééchantillonnées "
        "sur sa grille, et leurs bandes s'écrivent port_b1 ou port_nom (a_b1, b_red) ; avec une seule ligne dans une "
        "autre entrée, ce raster sert pour tous ceux de la première. Résultat en float32 par défaut."
    )
    rejected = False

    class Params(Params):
        inputs: list[str] = Field(
            default_factory=lambda: ["input"],
            title="Entrées",
            description="Noms des ports d'entrée (lettres, chiffres et _).",
            json_schema_extra={"default": ["input"]},
        )
        calculations: list[Calculation] = Field(
            default_factory=list, title="Bandes calculées", description="Une ligne par bande du résultat."
        )
        dtype: DTYPES = Field("float32", title="Type du résultat", description="Type des pixels du résultat.")
        nodata: float | None = Field(
            None,
            title="Valeur sans donnée",
            description="Vide = NaN pour un résultat décimal, le maximum du type sinon.",
        )
        resampling: Literal["nearest", "bilinear", "cubic", "average"] = Field(
            "bilinear", title="Rééchantillonnage", description="Pour aligner les entrées suivantes sur la première."
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        names = [n for n in getattr(params, "inputs", None) or [] if isinstance(n, str) and n]
        return (tuple(dict.fromkeys(names)) or cls.inputs), cls.outputs

    def _ports(self) -> list[str]:
        names = self.params.inputs
        if not names:
            raise ConfigError("au moins une entrée est nécessaire")
        for n in names:
            if not IDENT.match(n):
                raise ConfigError(f"nom d'entrée invalide : « {n} » (lettres, chiffres et _)")
        if len(set(names)) != len(names):
            raise ConfigError("entrée en double")
        return list(names)

    def _calculations(self) -> list[Calculation]:
        calcs = self.params.calculations
        if not calcs:
            raise ConfigError("au moins une bande calculée est nécessaire")
        for c in calcs:
            if not IDENT.match(c.name):
                raise ConfigError(f"nom de bande invalide : « {c.name} » (lettres, chiffres et _)")
            if not c.expression.strip():
                raise ConfigError(f"la bande {c.name} n'a pas d'expression")
        if len({c.name for c in calcs}) != len(calcs):
            raise ConfigError("bande en double")
        return calcs

    def _check_expressions(self, available: list[str]) -> None:
        dummy = {n: np.ones((2, 2), dtype=np.float32) for n in available}
        for c in self._calculations():
            unknown = sorted({w for w in _WORD.findall(c.expression) if w not in dummy and w not in _FUNCTIONS})
            if unknown:
                raise ConfigError(f"bande ou fonction inconnue dans « {c.name} » : {', '.join(unknown)}")
            try:
                numexpr.evaluate(c.expression, local_dict=dummy, global_dict={}, sanitize=True)
            except Exception as exc:  # noqa: BLE001 - numexpr lève des erreurs de plusieurs types
                raise ConfigError(f"expression invalide « {c.expression} » : {exc}") from exc

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._ports()
        self._calculations()
        for port in self.params.inputs:
            core.check_raster_schema(inputs.get(port))
        first = inputs.get(self.params.inputs[0])
        return {"output": first if first is not None else core.raster_schema()}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        ports = self._ports()
        calcs = self._calculations()
        tables: dict[str, pa.Table] = {}
        for port in ports:
            table = collect(inputs.get(port, ()))
            if table is None or table.num_rows == 0:
                self.ctx.warning(f"entrée sans raster : {port} ; rien n'est produit")
                return
            tables[port] = table
        descs = {port: core.descriptors(t) for port, t in tables.items()}
        base_attrs = core.attributes_of(tables[ports[0]])
        multi = len(ports) > 1
        outputs, kept = [], []
        for row, ref in enumerate(descs[ports[0]]):
            self.ctx.check_cancel()
            chosen: dict[str, dict[str, Any]] = {ports[0]: ref}
            for port in ports[1:]:
                rows = descs[port]
                chosen[port] = rows[0] if len(rows) == 1 else rows[row] if row < len(rows) else rows[-1]
            names: dict[str, tuple[str, int]] = {}  # variable -> (fichier, bande)
            for port, desc in chosen.items():
                for var, index in band_variables(port, desc, port == ports[0], multi).items():
                    names[var] = (desc["uri"], index)
            self._check_expressions(list(names))
            used = {w for c in calcs for w in _WORD.findall(c.expression)} & set(names)
            dtype = p.dtype
            nodata = core.nodata_for(dtype, p.nodata)
            grid = core.grid_of(ref)
            sources = {var: names[var] for var in used}

            def compute(window, cache, sources=sources, grid=grid, dtype=dtype, nodata=nodata, ref=ref):
                arrays: dict[str, np.ndarray] = {}
                valid = np.ones((window.height, window.width), dtype=bool)
                for var, (uri, index) in sources.items():
                    ds = cache.aligned(uri, grid, p.resampling) if uri != ref["uri"] else cache.open(uri)
                    values, ok = core.read_window(ds, window, [index])
                    arrays[var] = values[0]
                    valid &= ok[0]
                bands = []
                for c in calcs:
                    result = numexpr.evaluate(c.expression, local_dict=arrays, global_dict={}, sanitize=True)
                    result = np.broadcast_to(np.asarray(result, dtype=np.float64), valid.shape)
                    bands.append(
                        core.valid_to_nodata(
                            np.where(np.isfinite(result), result, np.nan if nodata is None else nodata),
                            valid,
                            nodata,
                            dtype,
                        )
                    )
                return np.stack(bands)

            desc = core.run_blocks(
                self.ctx,
                f"calc_{row}",
                grid,
                compute,
                count=len(calcs),
                dtype=dtype,
                nodata=nodata,
                band_names=[c.name for c in calcs],
            )
            outputs.append(desc)
            kept.append(row)
        attributes = {k: [v[i] for i in kept] for k, v in base_attrs.items()}
        emit("output", core.to_table(outputs, attributes))
        self.ctx.info(f"{len(outputs)} raster(s) calculé(s)")


_FUNCTIONS = {
    "where",
    "abs",
    "sqrt",
    "exp",
    "log",
    "log10",
    "log1p",
    "expm1",
    "sin",
    "cos",
    "tan",
    "arcsin",
    "arccos",
    "arctan",
    "arctan2",
    "sinh",
    "cosh",
    "tanh",
    "floor",
    "ceil",
    "minimum",
    "maximum",
    "isnan",
    "isfinite",
    "real",
    "imag",
    "contains",
    "sum",
    "prod",
    "True",
    "False",
    "int",
    "float",
    "bool",
}
