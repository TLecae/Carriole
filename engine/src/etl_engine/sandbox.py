"""Exécution isolée du code Python des flux en mode serveur (ADR 0053).

Le code d'un utilisateur ne tourne pas dans le worker (qui voit la base, les secrets, le cache de tous) mais dans un
processus fils, lancé par un bac à sable :
- `ETL_SANDBOX=bwrap` (défaut) : bubblewrap (Linux) ; le fils ne voit que Python et ses bibliothèques, en lecture
  seule, un `/tmp` vide, aucun réseau, aucune variable d'environnement du serveur, aucun autre processus ; il meurt
  avec le worker ;
- `ETL_SANDBOX=process` : un simple processus fils, sans isolation (essais, développement) ;
- toute autre valeur : la commande d'un autre outil d'isolation, à laquelle Carriole ajoute celle du fils.

Les lots passent par l'entrée et la sortie standard du fils, au format Arrow IPC ; `ctx.info` / `ctx.warning`
remontent avec chaque réponse, `ctx.state` vit dans le fils.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import IO, Any

import pyarrow as pa

from etl_engine.sdk import ConfigError

CHILD = ["-I", "-m", "etl_engine.sandbox_child"]
_HEAD = struct.Struct("<IQ")  # longueur de l'en-tête JSON, longueur des données Arrow
_KEPT_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")


# ------------------------------------------------------------------------------------------------- protocole


def send(stream: IO[bytes], header: dict[str, Any], payload: bytes = b"") -> None:
    data = json.dumps(header, ensure_ascii=False).encode()
    stream.write(_HEAD.pack(len(data), len(payload)) + data + payload)
    stream.flush()


def _exact(stream: IO[bytes], size: int) -> bytes:
    chunks, left = [], size
    while left:
        chunk = stream.read(left)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def receive(stream: IO[bytes]) -> tuple[dict[str, Any], bytes]:
    size, payload = _HEAD.unpack(_exact(stream, _HEAD.size))
    header = json.loads(_exact(stream, size))
    return header, _exact(stream, payload) if payload else b""


def to_ipc(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def from_ipc(data: bytes) -> pa.Table:
    return pa.ipc.open_stream(data).read_all()


# -------------------------------------------------------------------------------------------- bac à sable


def _python_dirs() -> list[str]:
    """Préfixes du Python du worker, sous leurs deux formes (chemin donné et chemin résolu) : un venv dont `python` est
    un lien vers un Python installé ailleurs (uv, pyenv) ne s'exécute que si chaque étape du lien existe aussi dans le
    bac à sable."""
    import site

    out = [sys.prefix, sys.base_prefix, sys.exec_prefix, *site.getsitepackages()]
    path = os.path.abspath(sys.executable)
    for _ in range(16):  # chaîne de liens : venv/bin/python -> …/cpython-3.12/bin/python3.12 -> …
        out.append(os.path.dirname(os.path.dirname(path)))
        if not os.path.islink(path):
            break
        target = os.readlink(path)
        path = os.path.normpath(target if os.path.isabs(target) else os.path.join(os.path.dirname(path), target))
    out.append(str(Path(__file__).resolve().parents[1]))  # dossier d'etl_engine (installation éditable)
    return [*out, *(str(Path(p).resolve()) for p in out)]


def _bwrap_args(proc: bool = True) -> list[str]:
    """Système en lecture seule (Python, ses bibliothèques), rien d'autre : ni données, ni base, ni réseau.
    `proc` faux : pas de `/proc` (un conteneur Docker interdit d'en monter un neuf ; Python s'en passe)."""
    args = ["--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
    args += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin", "--setenv", "HOME", "/tmp"]
    args += ["--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONUTF8", "1"]
    args += ["--ro-bind", "/usr", "/usr"]
    for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        if os.path.islink(top):
            args += ["--symlink", os.readlink(top), top]
        elif os.path.isdir(top):
            args += ["--ro-bind", top, top]
    for path in ("/etc/ld.so.cache", "/etc/localtime"):
        args += ["--ro-bind-try", path, path]
    # Python et ses paquets ; jamais `sys.path` en entier : il contient le dossier courant du worker
    seen: set[str] = set()
    for path in _python_dirs():
        path = os.path.normpath(path)
        if path != "/" and os.path.isdir(path) and path not in seen and not path.startswith("/usr/"):
            seen.add(path)
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc"] if proc else ["--dir", "/proc"]
    args += ["--dev", "/dev", "--tmpfs", "/tmp", "--chdir", "/tmp"]
    return args


def _try_bwrap(exe: str, proc: bool) -> str:
    """Vide si le bac à sable démarre Python ; sinon la dernière ligne de la réponse de bubblewrap."""
    try:
        done = subprocess.run(  # noqa: S603 - commande construite ici, sans shell
            [exe, *_bwrap_args(proc), "--", sys.executable, "-I", "-c", "pass"], capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if done.returncode == 0:
        return ""
    lines = done.stderr.decode(errors="replace").strip().splitlines()
    return lines[-1] if lines else f"code {done.returncode}"


@functools.cache
def _bwrap_mode(exe: str) -> tuple[bool | None, str]:
    """(avec `/proc` ?, problème) : essayé une fois par processus ; `None` : bubblewrap inutilisable ici."""
    problem = _try_bwrap(exe, proc=True)
    if not problem:
        return True, ""
    if "proc" in problem.lower() and not _try_bwrap(exe, proc=False):
        return False, ""
    return None, problem


def command(python: str) -> list[str]:
    """Commande qui lance le fils ; `ConfigError` si le bac à sable demandé n'existe pas sur cette machine."""
    kind = os.environ.get("ETL_SANDBOX", "bwrap").strip() or "bwrap"
    child = [python, *CHILD]
    if kind == "process":
        return child
    if kind == "bwrap":
        exe = shutil.which("bwrap")
        if not exe or not sys.platform.startswith("linux"):
            raise ConfigError(
                "aucun bac à sable pour le code Python des flux : installer bubblewrap (bwrap) sur le serveur, "
                "ou régler ETL_USER_CODE (allow ou deny)"
            )
        proc, problem = _bwrap_mode(exe)
        if proc is None:
            raise ConfigError(
                f"bac à sable bubblewrap inutilisable sur ce serveur ({problem}) : il lui faut les espaces de noms "
                "utilisateur (voir le guide du serveur), ou régler ETL_USER_CODE"
            )
        return [exe, *_bwrap_args(proc), "--", *child]
    return [*shlex.split(kind), *child]


class SandboxedScript:
    """Le code d'un PythonScript, chargé dans un processus isolé ; un appel par lot, dans l'ordre."""

    def __init__(self, code: str, variables: dict[str, Any]) -> None:
        env = {k: os.environ[k] for k in _KEPT_ENV if k in os.environ}
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(  # noqa: S603 - commande construite ici, sans shell
            command(sys.executable), stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, creationflags=flags
        )
        self._request({"op": "init", "code": code, "variables": variables})

    def _request(self, header: dict[str, Any], payload: bytes = b"") -> dict[str, Any]:
        assert self.proc.stdin and self.proc.stdout
        try:
            send(self.proc.stdin, header, payload)
            reply, _ = receive(self.proc.stdout)
        except (EOFError, BrokenPipeError, OSError):
            code = self.proc.poll()
            raise RuntimeError(
                f"le code Python s'est arrêté (code {code}) : mémoire épuisée ou processus tué"
            ) from None
        if "error" in reply:
            kind = ConfigError if reply.get("kind") == "config" else RuntimeError
            raise kind(reply["error"])
        return reply

    def call(self, batch: pa.Table) -> tuple[dict[str, pa.Table], list[tuple[str, str]]]:
        reply = self._request({"op": "batch"}, to_ipc(batch))
        outputs: dict[str, pa.Table] = {}
        assert self.proc.stdout
        for port in reply.get("ports", []):
            header, data = receive(self.proc.stdout)
            outputs[header.get("port", port)] = from_ipc(data)
        return outputs, [(str(k), str(m)) for k, m in reply.get("messages", [])]

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin
                send(self.proc.stdin, {"op": "close"})
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired, AssertionError):
                self.proc.kill()
                self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                with contextlib.suppress(OSError):  # tube d'un fils déjà mort
                    stream.close()
