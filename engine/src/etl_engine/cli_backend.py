"""Commandes `etl` qui parlent au backend local : démarrage, arrêt, runs, ouverture d'un flux."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from etl_engine import atomic, backend
from etl_engine.backend import BackendError, ServerInfo
from etl_engine.cli_io import say

EXIT_ERROR, EXIT_CANCELLED, EXIT_USAGE = 1, 2, 3
FINAL = ("done", "error", "cancelled", "interrupted")

runs_app = typer.Typer(help="Runs du backend local.", no_args_is_help=True)


def require_server() -> ServerInfo:
    info = backend.running_server()
    if info is None:
        say("Le backend n'est pas démarré : ouvrir l'application, ou lancer « etl serve ».", err=True)
        raise typer.Exit(EXIT_ERROR)
    return info


def start_detached(timeout: float = 40.0) -> ServerInfo:
    """Démarre `etl serve` en arrière-plan (sans fenêtre) et attend qu'il réponde."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(  # noqa: S603 - notre propre interpréteur
        [sys.executable, "-m", "etl_engine.cli", "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = backend.running_server()
        if info:
            return info
        time.sleep(0.3)
    say("Le backend n'a pas démarré à temps (voir les journaux dans le dossier de l'application).", err=True)
    raise typer.Exit(EXIT_ERROR)


def _call(info: ServerInfo, method: str, path: str, body: Any = None) -> Any:
    try:
        return backend.call(info, method, path, body)
    except BackendError as exc:
        say(f"Erreur{f' {exc.status}' if exc.status else ''} : {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from None


def register(app: typer.Typer) -> None:
    app.add_typer(runs_app, name="runs")

    @app.command()
    def serve(
        port: Annotated[int, typer.Option(help="Port (0 = un port libre).")] = 0,
        workers: Annotated[
            int | None, typer.Option(help="Nombre de workers (défaut : réglage « workers », sinon 2).")
        ] = None,
        json_output: Annotated[
            bool, typer.Option("--json", help="Annonce l'adresse et le jeton en JSON sur la sortie.")
        ] = False,
    ) -> None:
        """Démarre le backend local (API + workers) et reste au premier plan jusqu'à l'arrêt."""
        try:
            from etl_api.serve import run_server
        except ImportError:
            say("Le paquet du backend (etl-api) n'est pas installé.", err=True)
            raise typer.Exit(EXIT_USAGE) from None
        raise typer.Exit(run_server(port, workers, announce=json_output))

    @app.command()
    def status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
        """Dit si le backend local tourne. Code de sortie : 0 en marche, 1 arrêté."""
        info = backend.running_server()
        if json_output:
            say(json.dumps(info.as_json() if info else {"running": False}))
        elif info:
            say(f"En marche : {info.url} (pid {info.pid})")
        else:
            say("Arrêté")
        raise typer.Exit(0 if info else 1)

    @app.command()
    def stop(timeout: Annotated[float, typer.Option(help="Secondes d'attente de l'arrêt.")] = 90.0) -> None:
        """Arrête le backend local ; un run en cours est interrompu proprement (reprenable depuis son cache)."""
        info = require_server()
        _call(info, "POST", "/api/shutdown")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not backend.is_healthy(info, timeout=0.5) and not backend.info_path().exists():
                say("Arrêté")
                return
            time.sleep(0.3)
        say("Le backend s'arrête encore (des runs sont en cours d'interruption).", err=True)
        raise typer.Exit(EXIT_ERROR)

    @app.command("open")
    def open_flow(
        flow_file: Annotated[Path | None, typer.Argument(metavar="[FLUX]", help="Fichier de flux à ouvrir.")] = None,
    ) -> None:
        """Ouvre l'application (démarre le backend au besoin), sur un flux si indiqué."""
        info = backend.running_server() or start_detached()
        url = info.login_url
        if flow_file is not None:
            opened = _call(info, "POST", "/api/flows/open", {"path": str(flow_file.resolve())})
            url += f"#flow={opened['id']}"
        webbrowser.open(url)
        say(info.url)

    # ------------------------------------------------------------------ assistant
    @app.command("ai")
    def ai(
        question: Annotated[str, typer.Argument(help="Ce que l'assistant doit faire ou expliquer.")],
        flow: Annotated[Path | None, typer.Option("--flow", help="Flux de départ (JSON).")] = None,
        out: Annotated[
            Path | None, typer.Option("--out", help="Écrit ici le flux modifié si l'assistant en propose un.")
        ] = None,
        provider: Annotated[
            str | None, typer.Option("--provider", help="Fournisseur (défaut : celui des réglages).")
        ] = None,
        chat: Annotated[str | None, typer.Option("--chat", help="Poursuit une conversation de l'application.")] = None,
    ) -> None:
        """Interroge l'assistant IA (fournisseur et clé de l'application). N'écrit un flux que si --out est donné."""
        info = require_server()
        doc = json.loads(flow.read_text(encoding="utf-8")) if flow else {"name": "", "nodes": [], "links": []}
        config = _call(info, "GET", "/api/assistant/config")
        chosen = provider or config.get("default")
        if not chosen:
            say("Aucun fournisseur configuré : ouvrir l'application, Assistant, réglages.", err=True)
            raise typer.Exit(EXIT_ERROR)
        chat_id = chat or _call(info, "POST", "/api/assistant/chats", {"provider": chosen})["id"]
        failed = False
        try:
            for event in backend.stream_events(
                info, f"/api/assistant/chats/{chat_id}/messages", {"text": question, "flow": doc, "provider": chosen}
            ):
                kind = event["type"]
                if kind == "text":
                    say(event["delta"], nl=False)
                elif kind == "tool":
                    say(f"{chr(10)}  · {event['summary']}", err=True)
                elif kind == "error":
                    say("", err=True)
                    say(f"Erreur : {event['message']}", err=True)
                    failed = True
                elif kind == "action":
                    data = event["data"]
                    if event["name"] == "propose_flow_edits":
                        say(chr(10) + "Modifications proposées :")
                        for line in data["summary"]:
                            say(f"  - {line}")
                        if out:
                            atomic.write_text(out, json.dumps(data["flow"], ensure_ascii=False, indent=1) + chr(10))
                            say(f"Flux écrit : {out}")
                        else:
                            say("(ajouter --out fichier.json pour écrire le flux modifié)")
                    else:
                        say("")
                        say(f"Proposition « {event['name']} » : à valider dans l'application.")
        except BackendError as exc:
            say(f"Erreur{f' {exc.status}' if exc.status else ''} : {exc}", err=True)
            raise typer.Exit(EXIT_ERROR) from None
        say("")
        if failed:
            raise typer.Exit(EXIT_ERROR)

    # ------------------------------------------------------------------ runs
    @runs_app.command("list")
    def runs_list(
        status_: Annotated[str | None, typer.Option("--status", help="queued, running, done, error…")] = None,
        limit: Annotated[int, typer.Option()] = 20,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Liste les derniers runs."""
        query = f"?limit={limit}" + (f"&status={status_}" if status_ else "")
        rows = _call(require_server(), "GET", f"/api/runs{query}")
        if json_output:
            say(json.dumps(rows, ensure_ascii=False, indent=2))
            return
        for r in rows:
            when = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M")
            say(f"{r['id'][:8]}  {when}  {r['status']:11} {r['owner']:12} {r['flow_name']}")

    @runs_app.command("show")
    def runs_show(
        run_id: Annotated[str, typer.Argument()],
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Détail d'un run : statut, durée, comptes par nœud, erreur."""
        run = _call(require_server(), "GET", f"/api/runs/{run_id}")
        if json_output:
            say(json.dumps(run, ensure_ascii=False, indent=2))
            return
        name, status, error = run["flow_name"], run["status"], run.get("error")
        say(f"{name} - {status} - {error}" if error else f"{name} - {status}")
        for node, info in ((run.get("result") or {}).get("nodes") or {}).items():
            rows = ", ".join(f"{p}: {n}" for p, n in (info.get("rows") or {}).items())
            say(f"  [{node}] {info.get('status')} {rows}")

    @runs_app.command("cancel")
    def runs_cancel(run_id: Annotated[str, typer.Argument()]) -> None:
        """Annule un run en attente ou en cours."""
        run = _call(require_server(), "POST", f"/api/runs/{run_id}/cancel")
        say(run["status"])


def submit(
    flow: dict[str, Any], params: dict[str, str], cache: bool, until: str | None, wait: bool, as_json: bool
) -> int:
    """Met un run en file dans le backend (visible dans l'interface). Avec `wait` : attend la fin."""
    info = require_server()
    run = _call(info, "POST", "/api/runs", {"flow": flow, "params": params, "cache": cache, "target": until})
    if as_json:
        say(json.dumps({"id": run["id"], "status": run["status"]}))
    else:
        say(f"Run {run['id']} en file : {info.url}/#run={run['id']}")
    if not wait:
        return 0
    seen = 0
    while True:
        events = _call(info, "GET", f"/api/runs/{run['id']}/events?stream=false&after={seen}")
        for e in events:
            seen = max(seen, e["id"])
            if e["type"] == "log" and e.get("level") != "INFO":
                say(f"[{e.get('node')}] {e['level']} {e['message']}", err=True)
        current = _call(info, "GET", f"/api/runs/{run['id']}")
        if current["status"] in FINAL:
            status, error = current["status"], current.get("error")
            say(f"Run {status} : {error}" if error else f"Run {status}")
            return {"done": 0, "cancelled": EXIT_CANCELLED}.get(current["status"], EXIT_ERROR)
        time.sleep(1.0)
