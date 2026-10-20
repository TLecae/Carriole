"""RunFlow : lance un flux (un autre fichier .json) pour chaque entité reçue."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine import batch, paths
from etl_engine.flow import Flow
from etl_engine.sdk import Attribute, ConfigError, Emit, FilePath, Modifier, Params

OUT_COLUMNS = ("_flow_status", "_flow_seconds", "_flow_error")


class ParamMapping(Params):
    name: str = Field("", title="Paramètre du flux lancé", description="Nom d'un paramètre publié du flux lancé.")
    attribute: Attribute = Field("", title="Attribut", description="Attribut de l'entité reçue qui donne la valeur.")
    value: str = Field("", title="Valeur fixe", description="Utilisée si aucun attribut n'est choisi.")


class RunFlow(Modifier):
    name = "RunFlow"
    reusable = False  # toujours relancé (voir Modifier.reusable)
    label = "Lancer un flux"
    category = "Lots et variables"
    description = "Exécute un autre flux pour chaque entité reçue, avec des paramètres tirés de ses attributs."
    help = (
        "Chaque entité reçue lance une exécution du flux indiqué (`etl run` dans un processus séparé : mémoire isolée, "
        "un plantage n'arrête pas les autres). Les paramètres publiés du flux lancé reçoivent la valeur d'un attribut "
        "de l'entité (ou une valeur fixe). Les entités dont le flux a réussi sortent sur « Succeeded », les autres sur "
        "« Failed » ; dans les deux cas elles gardent leurs attributs et reçoivent _flow_status, _flow_seconds et "
        "_flow_error. « En parallèle » règle le nombre d'exécutions simultanées. Pour un dossier de fichiers : "
        "ListFiles → RunFlow, avec le paramètre du flux lancé relié à l'attribut path."
    )
    outputs = ("Succeeded", "Failed")
    rejected = False
    stateful = True

    class Params(Params):
        flow: FilePath = Field(
            "", title="Flux à lancer", description="Fichier .json du flux.", examples=["D:\\flux\\import_commune.json"]
        )
        parameters: list[ParamMapping] = Field(
            default_factory=list, title="Paramètres", description="Valeur de chaque paramètre publié du flux lancé."
        )
        parallel: int = Field(1, ge=1, le=16, title="En parallèle", description="Exécutions simultanées (1 à 16).")
        stop_on_error: bool = Field(
            False,
            title="S'arrêter au premier échec",
            description="Coché : les exécutions restantes ne sont pas lancées après un échec.",
        )
        cache: bool = Field(
            False, title="Garder les données", description="Garde le cache de chaque exécution (consomme du disque)."
        )

    def _checked(self) -> None:
        p = self.params
        if not p.flow:
            raise ConfigError("flux à lancer non renseigné")
        names = [m.name for m in p.parameters]
        if any(not n for n in names):
            raise ConfigError("chaque paramètre demande un nom")
        if len(set(names)) != len(names):
            raise ConfigError("paramètre en double")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._checked()
        first = inputs.get("input")
        if first is None:
            return {"Succeeded": None, "Failed": None}
        missing = [m.attribute for m in self.params.parameters if m.attribute and m.attribute not in first.names]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")
        extra = [
            pa.field("_flow_status", pa.string()),
            pa.field("_flow_seconds", pa.float64()),
            pa.field("_flow_error", pa.string()),
        ]
        schema = pa.schema([*[f for f in first if f.name not in OUT_COLUMNS], *extra], metadata=first.metadata)
        return {"Succeeded": schema, "Failed": schema}

    def used_attributes(self) -> list[str]:
        return [m.attribute for m in self.params.parameters if m.attribute]

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._checked()
        p = self.params
        flow_path = os.path.abspath(p.flow)
        paths.check(flow_path, "read")
        if not os.path.isfile(flow_path):
            raise ConfigError(f"fichier introuvable : {flow_path}")
        try:
            declared = {x.name for x in Flow.load(flow_path).published_params}
        except (OSError, ValueError) as exc:
            raise ConfigError(f"flux illisible : {exc}") from exc
        unknown = [m.name for m in p.parameters if m.name not in declared]
        if unknown:
            raise ConfigError(f"paramètres inconnus du flux lancé : {', '.join(unknown)}")

        tables = [b for b in inputs.get("input", ()) if b.num_rows]
        if not tables:
            return
        table = pa.concat_tables(tables)
        table = table.drop_columns([c for c in OUT_COLUMNS if c in table.column_names])
        missing = [m.attribute for m in p.parameters if m.attribute and m.attribute not in table.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")

        values = {m.name: table.column(m.attribute).to_pylist() if m.attribute else None for m in p.parameters}
        items = []
        for row in range(table.num_rows):
            params = {}
            for m in p.parameters:
                column = values[m.name]
                raw = m.value if column is None else column[row]
                params[m.name] = "" if raw is None else str(raw)
            first = next(iter(params.values()), "")
            items.append(batch.BatchItem(params, os.path.basename(first) or f"ligne {row + 1}"))

        done = 0

        def progress(_index: int, result: batch.BatchResult) -> None:
            nonlocal done
            done += 1
            self.ctx.progress(done)
            state = "OK" if result.ok else f"ÉCHEC ({result.error})"
            self.ctx.log("INFO" if result.ok else "WARNING", f"[{done}/{len(items)}] {result.item.label} : {state}")

        # l'annulation du run arrête les exécutions en cours
        stop = threading.Event()
        finished = threading.Event()

        def watch() -> None:
            while not finished.wait(0.3):
                if self.ctx.cancel.is_set():
                    stop.set()

        threading.Thread(target=watch, daemon=True).start()
        try:
            results = batch.run_batch(
                flow_path,
                items,
                parallel=p.parallel,
                extra=["--cache"] if p.cache else [],
                stop_on_error=p.stop_on_error,
                cancel=stop,
                on_done=progress,
            )
        finally:
            finished.set()
        self.ctx.check_cancel()

        by_item = {id(r.item): r for r in results}
        status, seconds, errors = [], [], []
        for item in items:
            r = by_item.get(id(item))
            status.append("not_run" if r is None else "done" if r.ok else "error")
            seconds.append(None if r is None else r.seconds)
            errors.append(None if r is None else (r.error or None))
        table = table.append_column("_flow_status", pa.array(status, pa.string()))
        table = table.append_column("_flow_seconds", pa.array(seconds, pa.float64()))
        table = table.append_column("_flow_error", pa.array(errors, pa.string()))
        ok = pa.array([s == "done" for s in status])
        emit("Succeeded", table.filter(ok))
        emit("Failed", table.filter(pc.invert(ok)))
        failed = status.count("error") + status.count("not_run")
        message = f"{len(items) - failed} flux réussi(s), {failed} en échec"
        (self.ctx.warning if failed else self.ctx.info)(message)
