"""Dossiers autorisés pour lire et écrire (voir docs/securite.md)."""

import os

import pytest
from etl_engine import paths
from etl_engine.analysis import analyze
from etl_engine.executor import run_flow
from etl_engine.modifiers.reader import Reader
from etl_engine.modifiers.writer import Writer
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier, table

from conftest import make_flow


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    inbox, outbox, other = tmp_path / "in", tmp_path / "out", tmp_path / "ailleurs"
    for d in (inbox, outbox, other):
        d.mkdir()
    (inbox / "ok.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    (other / "secret.csv").write_text("a;b\n9;9\n", encoding="utf-8")
    monkeypatch.setenv("ETL_READ_ROOTS", os.pathsep.join([str(inbox), str(outbox)]))
    monkeypatch.setenv("ETL_WRITE_ROOTS", str(outbox))
    return inbox, outbox, other


def test_unrestricted_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("ETL_READ_ROOTS", raising=False)
    monkeypatch.delenv("ETL_WRITE_ROOTS", raising=False)
    assert paths.check("/n/importe/ou", "read") == "/n/importe/ou"
    assert paths.roots("write") is None


def test_reader_reads_inside_and_refuses_outside(sandbox):
    inbox, _, other = sandbox
    out = run_modifier(Reader, {"path": str(inbox / "ok.csv"), "delimiter": ";"})["output"]
    assert out.num_rows == 1
    with pytest.raises(ConfigError, match="accès refusé"):
        run_modifier(Reader, {"path": str(other / "secret.csv"), "delimiter": ";"})


def test_dot_dot_and_patterns_cannot_escape(sandbox):
    inbox, _, other = sandbox
    sneaky = str(inbox / ".." / "ailleurs" / "secret.csv")
    with pytest.raises(ConfigError, match="accès refusé"):
        run_modifier(Reader, {"path": sneaky, "delimiter": ";"})
    with pytest.raises(ConfigError, match="accès refusé"):
        run_modifier(Reader, {"path": str(inbox / ".." / "ailleurs" / "*.csv"), "delimiter": ";"})


def test_symlink_pointing_outside_is_refused(sandbox):
    inbox, _, other = sandbox
    link = inbox / "lien.csv"
    try:
        link.symlink_to(other / "secret.csv")
    except (OSError, NotImplementedError):
        pytest.skip("liens symboliques non disponibles sur ce poste")
    with pytest.raises(ConfigError, match="accès refusé"):
        run_modifier(Reader, {"path": str(link), "delimiter": ";"})


def test_writer_writes_only_in_write_roots(sandbox):
    inbox, outbox, _ = sandbox
    data = table(a=[1, 2])
    run_modifier(Writer, {"path": str(outbox / "sortie.csv")}, data)
    assert (outbox / "sortie.csv").exists()
    with pytest.raises(ConfigError, match="écrire"):
        run_modifier(Writer, {"path": str(inbox / "interdit.csv")}, data)  # lisible, mais pas inscriptible
    with pytest.raises(ConfigError, match="accès refusé"):
        run_modifier(Writer, {"path": str(outbox / ".." / "in" / "ok.csv")}, data)
    assert (inbox / "ok.csv").read_text(encoding="utf-8").startswith("a;b")  # intact


def test_error_is_reported_in_analysis_and_in_the_run(registry, sandbox, tmp_path):
    _, outbox, other = sandbox
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": str(other / "secret.csv"), "delimiter": ";"}},
            {"id": "w", "type": "Writer", "params": {"path": str(outbox / "x.csv")}},
        ],
        [("r", "output", "w", "input")],
    )
    report = analyze(flow, registry)
    assert any("accès refusé" in i["message"] for i in report["issues"])
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "error" and "accès refusé" in res.nodes["r"].error
    assert not (outbox / "x.csv").exists()
