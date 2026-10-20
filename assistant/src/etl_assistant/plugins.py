"""Installation d'un modificateur écrit par l'assistant : relecture par l'utilisateur, test isolé, puis installation.

Rien n'est exécuté avant le clic de l'utilisateur sur « Tester et installer ». Le test tourne dans un processus
séparé, dans un dossier temporaire, avec un délai ; le plugin n'est copié dans le dossier des plugins que s'il passe.
Le code tourne ensuite avec les droits de l'utilisateur, comme tout plugin (voir docs/securite.md).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from etl_assistant.tools import NAME_RE

TIMEOUT = 90


def module_name(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def run_tests(name: str, code: str, test_code: str) -> dict[str, Any]:
    """Lance le test du plugin dans un processus séparé ; renvoie `{"ok", "output"}`."""
    if not NAME_RE.match(name):
        return {"ok": False, "output": "nom invalide"}
    module = f"plugin_{module_name(name)}"
    with tempfile.TemporaryDirectory(prefix="etl-plugin-") as tmp:
        folder = Path(tmp)
        (folder / f"{module}.py").write_text(code, encoding="utf-8")
        (folder / f"test_{module}.py").write_text(test_code, encoding="utf-8")
        try:
            # -I : mode isolé (ni PYTHONPATH ni dossier courant dans sys.path) ; pytest ajoute lui-même le dossier du test
            done = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "pytest",
                    "-q",
                    "-x",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                    str(folder),
                ],
                cwd=folder,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"le test a dépassé {TIMEOUT} s"}
    output = (done.stdout + done.stderr).strip()
    return {"ok": done.returncode == 0, "output": output[-4000:]}


def install(name: str, code: str, plugins_dir: Path) -> Path:
    """Copie le plugin dans le dossier des plugins (ne remplace jamais un fichier existant)."""
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / f"{module_name(name)}.py"
    if target.exists():
        raise FileExistsError(f"{target.name} existe déjà dans le dossier des plugins")
    target.write_text(code, encoding="utf-8")
    return target
