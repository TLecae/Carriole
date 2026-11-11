"""Code Python des flux en mode serveur (ADR 0053) : politique d'exécution et processus isolé.

`ETL_SANDBOX=process` lance le même protocole sans isolation (Windows, essais) ; bubblewrap est testé sous Linux
quand il est installé.
"""

from __future__ import annotations

import shutil
import sys

import pytest
from etl_engine import sandbox, user_code
from etl_engine.modifiers.python_script import PythonScript
from etl_engine.sdk import ConfigError, Context
from etl_engine.testing import run_modifier, table

STATEFUL = """
def process(table, ctx):
    ctx.state["seen"] = ctx.state.get("seen", 0) + table.num_rows
    print("une ligne du code de l'utilisateur")  # ne doit pas casser le protocole
    ctx.info(f"{ctx.state['seen']} lignes, seuil {ctx.variables.get('seuil')}")
    ok = pc.greater(table.column("n"), 1)
    return {"output": table.filter(ok).append_column("vu", pa.array([ctx.state["seen"]] * table.filter(ok).num_rows)),
            "Rejected": reject(table.filter(pc.invert(ok)), "trop petit")}
"""


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("ETL_USER_CODE", "sandbox")
    monkeypatch.setenv("ETL_SANDBOX", "process")


class Ctx(Context):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.logged: list[tuple[str, str]] = []

    def info(self, message: str) -> None:  # type: ignore[override]
        self.logged.append(("info", message))

    def warning(self, message: str) -> None:  # type: ignore[override]
        self.logged.append(("warning", message))


def test_policy_defaults(monkeypatch):
    monkeypatch.delenv("ETL_USER_CODE", raising=False)
    monkeypatch.delenv("ETL_MODE", raising=False)
    assert user_code.policy() == "allow"
    monkeypatch.setenv("ETL_MODE", "server")
    assert user_code.policy() == "sandbox"
    monkeypatch.setenv("ETL_USER_CODE", "deny")
    assert user_code.policy() == "deny"


def test_sandboxed_script_keeps_state_messages_and_ports(isolated):
    ctx = Ctx(batch_rows=2)
    ctx.variables = {"seuil": "5"}
    out = run_modifier(PythonScript, {"code": STATEFUL}, table(n=[1, 2, 3, 4]), batch_rows=2, ctx=ctx)
    assert out["output"].column("n").to_pylist() == [2, 3, 4]
    assert out["output"].column("vu").to_pylist() == [2, 4, 4]  # état gardé d'un lot à l'autre, dans le fils
    assert out["Rejected"].column("_rejection_reason").to_pylist() == ["trop petit"]
    assert ("info", "4 lignes, seuil 5") in ctx.logged


def test_sandboxed_errors_keep_their_kind_and_line(isolated):
    with pytest.raises(RuntimeError, match=r"ZeroDivisionError.*ligne 3"):
        run_modifier(
            PythonScript, {"code": "def process(table, ctx):\n    # commentaire\n    return 1 / 0"}, table(n=[1])
        )
    with pytest.raises(ConfigError, match="sortie inconnue"):
        run_modifier(PythonScript, {"code": 'def process(table, ctx):\n    return {"autre": table}'}, table(n=[1]))
    with pytest.raises(ConfigError, match="ne se charge pas"):
        run_modifier(
            PythonScript, {"code": "import nexistepas\ndef process(table, ctx):\n    return table"}, table(n=[1])
        )


def test_sandboxed_child_does_not_inherit_the_server_environment(isolated, monkeypatch):
    monkeypatch.setenv("ETL_SECRET_KEY", "ne-doit-pas-fuiter")
    code = (
        "import os\n"
        "def process(table, ctx):\n"
        '    return pa.table({"k": [",".join(sorted(k for k in os.environ if k.startswith("ETL_")))]})'
    )
    out = run_modifier(PythonScript, {"code": code}, table(n=[1]))
    assert out["output"].column("k").to_pylist() == [""]


def test_a_killed_child_is_reported(isolated):
    code = "import os\ndef process(table, ctx):\n    os._exit(3)"
    with pytest.raises(RuntimeError, match="s'est arrêté"):
        run_modifier(PythonScript, {"code": code}, table(n=[1]))


def test_denied_or_unavailable_user_code_is_a_configuration_error(monkeypatch):
    script = PythonScript(PythonScript.Params(code="def process(table, ctx):\n    return table"), Context())
    monkeypatch.setenv("ETL_USER_CODE", "deny")
    with pytest.raises(ConfigError, match="désactivé"):
        script.output_schema({"input": None})
    monkeypatch.setenv("ETL_USER_CODE", "sandbox")
    monkeypatch.setenv("ETL_SANDBOX", "bwrap")
    if not (sys.platform.startswith("linux") and shutil.which("bwrap")):
        with pytest.raises(ConfigError, match="bubblewrap"):
            script.output_schema({"input": None})
    monkeypatch.setenv("ETL_USER_CODE", "allow")
    assert script.output_schema({"input": None}) == {"output": None}


@pytest.mark.skipif(not (sys.platform.startswith("linux") and shutil.which("bwrap")), reason="bubblewrap absent")
def test_bubblewrap_hides_files_and_network(monkeypatch, tmp_path):
    monkeypatch.setenv("ETL_USER_CODE", "sandbox")
    monkeypatch.setenv("ETL_SANDBOX", "bwrap")
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    code = f"""
import os, socket
def process(table, ctx):
    seen = os.path.exists({str(secret)!r})
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2)
        net = True
    except OSError:
        net = False
    return pa.table({{"fichier": [seen], "reseau": [net]}})
"""
    out = run_modifier(PythonScript, {"code": code}, table(n=[1]))
    assert out["output"].to_pylist() == [{"fichier": False, "reseau": False}]


def test_protocol_round_trips_tables():
    t = table(n=[1, 2], s=["a", None])
    assert sandbox.from_ipc(sandbox.to_ipc(t)).equals(t)


def test_an_unusable_bubblewrap_is_reported_with_its_reason(monkeypatch):
    class Done:
        returncode = 1
        stderr = b"bwrap: No permissions to create new namespace\n"

    sandbox._bwrap_mode.cache_clear()
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setenv("ETL_SANDBOX", "bwrap")
    try:
        with pytest.raises(ConfigError, match="No permissions to create new namespace"):
            sandbox.command("python")
    finally:
        sandbox._bwrap_mode.cache_clear()


def test_bubblewrap_falls_back_without_proc_in_a_container(monkeypatch):
    """Docker interdit de monter un /proc neuf : le bac à sable démarre alors sans /proc (Python s'en passe)."""
    calls = []

    class Done:
        def __init__(self, ok, err=b""):
            self.returncode, self.stderr = (0 if ok else 1), err

    def run(cmd, **_kwargs):
        with_proc = "--proc" in cmd
        calls.append(with_proc)
        return Done(not with_proc, b"bwrap: Can't mount proc on /newroot/proc: Operation not permitted\n")

    sandbox._bwrap_mode.cache_clear()
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    monkeypatch.setenv("ETL_SANDBOX", "bwrap")
    try:
        cmd = sandbox.command("python")
    finally:
        sandbox._bwrap_mode.cache_clear()
    assert calls == [True, False] and "--proc" not in cmd and cmd[cmd.index("--dir") + 1] == "/proc"


def test_python_link_chain_is_mounted(tmp_path, monkeypatch):
    """Un venv dont python est un lien vers un Python installé ailleurs : chaque étape du lien est montée."""
    import os

    if not hasattr(os, "symlink") or os.name == "nt":
        pytest.skip("liens symboliques POSIX")
    real = tmp_path / "pythons" / "cpython-3.12.9" / "bin"
    real.mkdir(parents=True)
    (real / "python3.12").write_text("")
    (tmp_path / "pythons" / "cpython-3.12").symlink_to(tmp_path / "pythons" / "cpython-3.12.9")
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(tmp_path / "pythons" / "cpython-3.12" / "bin" / "python3.12")
    monkeypatch.setattr(sandbox.sys, "executable", str(venv / "python"))
    dirs = sandbox._python_dirs()
    assert str(tmp_path / "pythons" / "cpython-3.12") in dirs and str(tmp_path / "pythons" / "cpython-3.12.9") in dirs
