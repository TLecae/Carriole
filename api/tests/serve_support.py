"""Démarrage d'un vrai backend de bureau (`etl serve`) pour les tests d'intégration (fixture `make_backend`).

Pas un `conftest.py` : les tests du moteur importent `conftest` et ne doivent pas le confondre avec celui-ci."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from etl_engine import backend


class Backend:
    def __init__(self, home: Path, workers: int, extra_env: dict[str, str], args: list[str] | None = None) -> None:
        self.home = home
        self.args = args or []
        self.env = {**os.environ, "ETL_HOME": str(home), "PYTHONUTF8": "1", **extra_env}
        self.env.pop("ETL_API_TOKEN", None)
        self.workers = workers
        self.proc: subprocess.Popen | None = None
        self.info: backend.ServerInfo | None = None

    def start(self) -> backend.ServerInfo:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "etl_engine.cli", "serve", "--workers", str(self.workers), "--json", *self.args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            text=True,
        )
        line = self.proc.stdout.readline()  # l'annonce n'arrive qu'une fois l'API prête
        assert line, self.proc.stderr.read()
        data = json.loads(line)
        self.info = backend.ServerInfo(data["pid"], data["port"], data["token"], data["started"])
        return self.info

    def call(self, method: str, path: str, body=None):
        return backend.call(self.info, method, path, body)

    def wait_status(self, run_id: str, wanted: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        current: dict = {}
        while time.monotonic() < deadline:
            current = self.call("GET", f"/api/runs/{run_id}")
            if current["status"] == wanted:
                return current
            time.sleep(0.5)
        raise AssertionError(
            f"run {run_id} : statut « {current.get('status')} » au lieu de « {wanted} » après {timeout} s"
        )

    def stop(self, timeout: float = 120) -> int:
        """Arrêt propre par l'API ; code de sortie du processus."""
        assert self.proc is not None
        if self.proc.poll() is None:
            self.call("POST", "/api/shutdown")
        code = self.proc.wait(timeout=timeout)
        if code != 0:  # le journal du backend explique l'échec
            raise AssertionError(f"etl serve s'est arrêté avec le code {code} :\n{self.proc.stderr.read()}")
        return code

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)


@pytest.fixture
def make_backend(tmp_path):
    """Fabrique de backends (un seul à la fois par `ETL_HOME`) ; tous sont arrêtés à la fin du test."""
    created: list[Backend] = []

    def factory(workers: int = 1, home: Path | None = None, args: list[str] | None = None, **extra_env: str) -> Backend:
        instance = Backend(home or tmp_path / "home", workers, extra_env, args)
        created.append(instance)
        instance.start()
        return instance

    yield factory
    for instance in created:
        instance.kill()
