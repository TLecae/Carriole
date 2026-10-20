"""Les commandes `etl` qui parlent au backend local, et les outils sans backend (inspect, config)."""

import json
import subprocess
import sys
from pathlib import Path

from serve_support import make_backend  # noqa: F401 - fixture


def etl(server_or_home, *args, check=False, timeout=120):
    """Lance la vraie ligne de commande, dans le même ETL_HOME que le backend."""
    import os

    home = server_or_home if isinstance(server_or_home, Path) else server_or_home.home  # Path.home existe aussi
    env = {**os.environ, "ETL_HOME": str(home), "PYTHONUTF8": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "etl_engine.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
    )
    if check:
        assert done.returncode == 0, done.stderr
    return done


def test_commands_without_backend_say_so(tmp_path):
    status = etl(tmp_path / "home", "status")
    assert status.returncode == 1 and "Arrêté" in status.stdout
    runs = etl(tmp_path / "home", "runs", "list")
    assert runs.returncode == 1 and "n'est pas démarré" in runs.stderr


def test_config_roundtrip(tmp_path):
    home = tmp_path / "home"
    etl(home, "config", "set", "workers", "3", check=True)
    etl(home, "config", "set", "ai.provider", "ollama", check=True)
    assert etl(home, "config", "get", "workers", check=True).stdout.strip() == "3"
    assert json.loads(etl(home, "config", "get", check=True).stdout) == {"ai": {"provider": "ollama"}, "workers": 3}
    etl(home, "config", "unset", "workers", check=True)
    assert etl(home, "config", "get", "workers").returncode == 1
    assert str(home) in etl(home, "config", "path", check=True).stdout


def test_inspect_describes_a_file(tmp_path):
    csv = tmp_path / "communes.csv"
    csv.write_text("insee;nom;pop\n31555;Toulouse;500000\n09122;Foix;10000\n", encoding="utf-8")
    described = json.loads(etl(tmp_path, "inspect", str(csv), "--count", "--json", check=True).stdout)
    assert described["format"] == "csv" and described["count"] == 2 and described["geometry"] is False
    assert [f["name"] for f in described["fields"]] == ["insee", "nom", "pop"]
    text = etl(tmp_path, "inspect", str(csv), check=True).stdout
    assert "Toulouse" not in text and "insee" in text  # décrit le schéma, n'affiche pas les données
    assert etl(tmp_path, "inspect", str(tmp_path / "absent.csv")).returncode == 1


def test_submit_wait_runs_a_flow_through_the_backend_and_runs_commands_see_it(make_backend, tmp_path):
    server = make_backend(workers=1)
    src = tmp_path / "in.csv"
    src.write_text("id\n1\n2\n3\n", encoding="utf-8")
    flow = {"name": "soumis", "nodes": [{"id": "r", "type": "Reader", "params": {"path": str(src)}}], "links": []}
    path = tmp_path / "soumis.json"
    path.write_text(json.dumps(flow), encoding="utf-8")

    assert etl(server, "status").returncode == 0
    done = etl(server, "run", str(path), "--submit", "--wait")
    assert done.returncode == 0 and "Run done" in done.stdout, done.stderr
    listed = etl(server, "runs", "list", "--json", check=True).stdout
    runs = json.loads(listed)
    assert runs and runs[0]["status"] == "done" and runs[0]["flow_name"] == "soumis"
    shown = etl(server, "runs", "show", runs[0]["id"], check=True).stdout
    assert "soumis - done" in shown and "[r] done" in shown

    bad = {"name": "casse", "nodes": [{"id": "r", "type": "Reader", "params": {"path": str(tmp_path / "x.csv")}}]}
    bad_path = tmp_path / "casse.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    failed = etl(server, "run", str(bad_path), "--submit", "--wait")
    assert failed.returncode == 1 and "Run error" in failed.stdout

    stopped = etl(server, "stop", check=True)
    assert "Arrêté" in stopped.stdout
    assert server.proc.wait(timeout=60) == 0
