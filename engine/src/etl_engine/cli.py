"""Ligne de commande `etl`."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Annotated

import typer

from etl_engine import __version__
from etl_engine import settings as user_settings
from etl_engine.analysis import analyze
from etl_engine.cli_io import say
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.registry import Registry

app = typer.Typer(help="Carriole : exécution de flux sans interface.", no_args_is_help=True)
modifiers_app = typer.Typer(help="Modifiers disponibles.", no_args_is_help=True)
app.add_typer(modifiers_app, name="modifiers")

EXIT_ERROR, EXIT_CANCELLED, EXIT_USAGE = 1, 2, 3


def _version(value: bool) -> None:
    if value:
        say(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Affiche la version.")
    ] = False,
    lang: Annotated[
        str | None, typer.Option("--lang", help="Langue des messages (fr, en…) ; défaut : réglage « language ».")
    ] = None,
) -> None:
    """Point d'entrée commun des commandes."""
    if lang:
        os.environ["ETL_LANG"] = lang
    with contextlib.suppress(ValueError):  # réglages illisibles : les commandes qui en ont besoin le diront
        user_settings.apply_environment()  # proxy et budget mémoire des réglages
    for stream in (sys.stdout, sys.stderr):  # sortie redirigée (tâche planifiée, fichier) : toujours en UTF-8
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _parse_params(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"« {item} » : attendu CLE=valeur")
        out[key] = value
    return out


def _load(flow_path: Path) -> Flow:
    try:
        return Flow.load(flow_path)
    except (OSError, ValueError) as exc:
        say(f"Flux illisible : {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from exc


@app.command()
def run(
    flow_file: Annotated[Path, typer.Argument(metavar="FLUX", help="Fichier JSON du flux.")],
    param: Annotated[list[str], typer.Option("--param", "-p", help="Paramètre publié : CLE=valeur (répétable).")] = [],  # noqa: B006
    cache: Annotated[
        bool | None,
        typer.Option(
            "--cache/--no-cache",
            help="Garde les données de chaque port (désactivé par défaut ; activé avec --submit, pour l'inspecteur).",
        ),
    ] = None,
    run_dir: Annotated[
        Path | None, typer.Option(help="Dossier du run (conservé). Sinon un dossier temporaire est supprimé.")
    ] = None,
    reuse_from: Annotated[
        Path | None, typer.Option(help="Run précédent dont on réutilise les nœuds inchangés.")
    ] = None,
    until: Annotated[str | None, typer.Option("--until", help="Ne lance que ce nœud et son amont.")] = None,
    plugins: Annotated[Path | None, typer.Option(help="Dossier de plugins (défaut : ETL_PLUGINS_DIR).")] = None,
    workers: Annotated[int | None, typer.Option(help="Nombre maximal de nœuds en parallèle.")] = None,
    timeout: Annotated[float | None, typer.Option(help="Délai maximal en secondes.")] = None,
    json_events: Annotated[bool, typer.Option("--json", help="Un événement JSON par ligne au lieu du texte.")] = False,
    log_file: Annotated[Path | None, typer.Option(help="Ajoute aussi les événements, en texte, à ce fichier.")] = None,
    submit: Annotated[
        bool, typer.Option("--submit", help="Met le run dans la file du backend local (visible dans l'application).")
    ] = False,
    wait: Annotated[
        bool, typer.Option("--wait", help="Avec --submit : attend la fin du run et renvoie son code.")
    ] = False,
    each: Annotated[
        str | None,
        typer.Option("--each", help="Lot : un run par fichier du motif (ex. D:\\dossier\\*.shp), avec --as."),
    ] = None,
    each_list: Annotated[
        Path | None, typer.Option("--each-list", help="Lot : un run par ligne de ce fichier texte, avec --as.")
    ] = None,
    each_csv: Annotated[
        Path | None, typer.Option("--each-csv", help="Lot : un run par ligne de ce CSV (colonnes = paramètres).")
    ] = None,
    as_param: Annotated[
        str | None, typer.Option("--as", help="Lot : paramètre publié qui reçoit chaque fichier ou chaque ligne.")
    ] = None,
    parallel: Annotated[int, typer.Option("--parallel", min=1, max=16, help="Lot : runs simultanés.")] = 1,
    stop_on_error: Annotated[
        bool, typer.Option("--stop-on-error", help="Lot : ne lance plus de run après un échec.")
    ] = False,
) -> None:
    """Exécute un flux. Code de sortie : 0 terminé, 1 erreur, 2 annulé, 3 usage."""
    flow = _load(flow_file)
    if each or each_list or each_csv:
        raise typer.Exit(
            _run_batch(
                flow_file,
                flow,
                _parse_params(param),
                each,
                each_list,
                each_csv,
                as_param,
                parallel,
                stop_on_error,
                cache,
                plugins,
                timeout,
            )
        )
    if submit:
        from etl_engine.cli_backend import submit as submit_run

        raise typer.Exit(
            submit_run(
                flow.model_dump(mode="json", by_alias=True),
                _parse_params(param),
                cache is not False,
                until,
                wait,
                json_events,
            )
        )
    registry = Registry.discover(plugins)
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: cancel.set())

    def show(e: dict) -> None:
        if log_file is not None:
            with open(log_file, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {json.dumps(e, ensure_ascii=False)}\n")
        if json_events:
            say(json.dumps(e, ensure_ascii=False))
            return
        if e["type"] == "node" and e["status"] != "running":
            rows = e.get("rows") or e.get("rows_in") or {}
            counts = ", ".join(f"{p}: {n}" for p, n in rows.items())
            extra = f" - {e['error']}" if e.get("error") else ""
            say(f"[{e['node']}] {e['status']} ({e.get('seconds', 0)} s) {counts}{extra}")
        elif e["type"] == "log" and e["level"] != "INFO":
            say(f"[{e['node']}] {e['level']} {e['message']}", err=True)
        elif e["type"] == "run_finished":
            status, seconds, error = e["status"], e["seconds"], e.get("error")
            say(f"Run {status} en {seconds} s : {error}" if error else f"Run {status} en {seconds} s")

    cache = bool(cache)
    keep = run_dir is not None or cache
    directory = run_dir or Path(tempfile.mkdtemp(prefix="etl-run-"))
    try:
        result = run_flow(
            flow,
            registry,
            directory,
            params=_parse_params(param),
            cache=cache,
            reuse_from=reuse_from,
            target=until,
            on_event=show,
            cancel=cancel,
            max_workers=workers,
            timeout=timeout,
            flow_path=flow_file,
        )
    finally:
        if not keep:
            shutil.rmtree(directory, ignore_errors=True)
    raise typer.Exit({"done": 0, "error": EXIT_ERROR, "cancelled": EXIT_CANCELLED}[result.status])


def _run_batch(
    flow_file: Path,
    flow: Flow,
    base: dict[str, str],
    each: str | None,
    each_list: Path | None,
    each_csv: Path | None,
    as_param: str | None,
    parallel: int,
    stop_on_error: bool,
    cache: bool | None,
    plugins: Path | None,
    timeout: float | None,
) -> int:
    """`etl run flux.json --each …` : le même flux, un run par fichier ou par ligne, éventuellement en parallèle."""
    from etl_engine import batch

    if sum(x is not None for x in (each, each_list, each_csv)) != 1:
        say("Choisir un seul mode de lot : --each, --each-list ou --each-csv.", err=True)
        return EXIT_USAGE
    declared = {p.name for p in flow.published_params}
    if each is not None or each_list is not None:
        if not as_param:
            say("--as est nécessaire : le paramètre publié qui reçoit chaque valeur.", err=True)
            return EXIT_USAGE
        if as_param not in declared:
            say(f"Le flux n'a pas de paramètre publié « {as_param} ».", err=True)
            return EXIT_USAGE
    try:
        if each is not None:
            items = batch.items_from_values(as_param or "", batch.expand_each(each))
        elif each_list is not None:
            items = batch.items_from_list(as_param or "", each_list)
        else:
            items = batch.items_from_csv(each_csv or Path())
    except (OSError, ValueError) as exc:
        say(f"Lot illisible : {exc}", err=True)
        return EXIT_USAGE
    if not items:
        say("Le lot est vide : aucun fichier ni aucune ligne.", err=True)
        return EXIT_USAGE
    unknown = sorted({k for item in items for k in item.params} - declared)
    if unknown:
        say(f"Paramètres inconnus du flux : {', '.join(unknown)}", err=True)
        return EXIT_USAGE
    for item in items:
        item.params = {**base, **item.params}
    extra: list[str] = ["--cache"] if cache else []
    if plugins:
        extra += ["--plugins", str(plugins)]
    if timeout:
        extra += ["--timeout", str(timeout)]
    total = len(items)
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    done = 0

    def report(_index: int, result: batch.BatchResult) -> None:
        nonlocal done
        done += 1
        label, seconds, error = result.item.label, result.seconds, result.error
        if result.ok:
            say(f"[{done}/{total}] {label} : terminé ({seconds} s)")
        else:
            say(f"[{done}/{total}] {label} : ÉCHEC ({seconds} s) {error}", err=True)

    say(f"{total} run(s), {parallel} en parallèle")
    results = batch.run_batch(
        str(flow_file),
        items,
        parallel=parallel,
        extra=extra,
        stop_on_error=stop_on_error,
        cancel=cancel,
        on_done=report,
    )
    failed = sum(1 for r in results if not r.ok)
    skipped = total - len(results)
    succeeded = len(results) - failed
    say(f"Lot terminé : {succeeded} réussi(s), {failed} en échec, {skipped} non lancé(s)")
    if cancel.is_set() and failed == 0:
        return EXIT_CANCELLED
    return EXIT_ERROR if failed or skipped else 0


@app.command()
def validate(
    flow_file: Annotated[Path, typer.Argument(metavar="FLUX")],
    param: Annotated[list[str], typer.Option("--param", "-p")] = [],  # noqa: B006
    plugins: Annotated[Path | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Vérifie un flux sans l'exécuter : paramètres manquants, attributs absents en amont, nœuds non connectés."""
    report = analyze(_load(flow_file), Registry.discover(plugins), _parse_params(param), None)
    if json_output:
        say(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for i in report["issues"]:
            say(f"{i['level']:7} {i['node'] or '-':12} {i['message']}")
        say(f"{len(report['issues'])} point(s)")
    raise typer.Exit(EXIT_ERROR if any(i["level"] == "error" for i in report["issues"]) else 0)


@modifiers_app.command("list")
def list_modifiers(plugins: Annotated[Path | None, typer.Option()] = None) -> None:
    """Liste les modificateurs disponibles (intégrés, plugins, entry points)."""
    registry = Registry.discover(plugins)
    for cls in registry.all():
        say(f"{cls.category:20} {cls.name:24} {cls.label}")
    for source, message in registry.errors.items():
        say(f"plugin ignoré : {source} : {message}", err=True)


@modifiers_app.command("doc")
def doc_modifiers(
    out: Annotated[Path, typer.Option(help="Fichier Markdown à écrire.")] = Path("docs/catalogue.md"),
    plugins: Annotated[Path | None, typer.Option()] = None,
    doc_lang: Annotated[str, typer.Option("--lang", help="Langue du catalogue (fr, en…).")] = "fr",
) -> None:
    """Génère la page de documentation du catalogue (paramètres, ports, aides)."""
    from etl_engine.catalogue import render

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(
        render(Registry.discover(plugins), doc_lang).encode("utf-8")
    )  # fins de ligne LF sur tous les postes
    say(f"catalogue écrit dans {out}")


_SKELETON = '''"""{label} : {description}"""

from __future__ import annotations

import pyarrow as pa

from etl_engine.sdk import Attribute, Params, Modifier


class {cls}(Modifier):
    name = "{cls}"
    label = "{label}"
    category = "{category}"
    description = "{description}"
    help = "Explique ici, en français, ce que fait le modificateur et comment le régler."

    class Params(Params):
        attribute: Attribute = ""  # un attribut présent en amont : l'éditeur propose la liste

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        # `batch` : un lot d'entités (table Arrow). Rendre un lot par port de sortie.
        # Pour rejeter des entités : {{"Rejected": reject(lignes, "motif")}} (from etl_engine.sdk import reject).
        return {{"output": batch}}
'''

_TEST = """from etl_engine.testing import run_modifier, table

from {module} import {cls}


def test_{module}_passe_les_entites():
    entree = table(a=[1, 2, 3, 4, 5])
    sortie = run_modifier({cls}, {{"attribute": "a"}}, entree)
    assert sortie["output"].num_rows == 5
"""


@app.command("new-modifier")
def new_modifier(
    name: Annotated[str, typer.Argument(help="Nom en CamelCase, ex. SurfaceCalculator.")],
    directory: Annotated[Path, typer.Option("--dir", help="Dossier de plugins.")] = Path("plugins"),
    category: Annotated[str, typer.Option()] = "Divers",
    label: Annotated[str | None, typer.Option(help="Libellé affiché (défaut : le nom).")] = None,
    description: Annotated[str, typer.Option()] = "À décrire.",
) -> None:
    """Crée le squelette d'un modificateur et son test dans un dossier de plugins."""
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", name):
        raise typer.BadParameter("le nom doit être en CamelCase (lettres et chiffres)")
    module = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    directory.mkdir(parents=True, exist_ok=True)
    target, test = directory / f"{module}.py", directory / f"test_{module}.py"
    for path in (target, test):
        if path.exists():
            say(f"{path} existe déjà", err=True)
            raise typer.Exit(EXIT_USAGE)
    target.write_text(
        _SKELETON.format(cls=name, label=label or name, category=category, description=description), encoding="utf-8"
    )
    test.write_text(_TEST.format(cls=name, module=module), encoding="utf-8")
    say(f"créé : {target}\ncréé : {test}\ntest : pytest {test}")


from etl_engine import cli_backend, cli_tools  # noqa: E402 - enregistrent leurs commandes sur `app`

cli_backend.register(app)
cli_tools.register(app)


if __name__ == "__main__":
    app()
