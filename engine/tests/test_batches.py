import json
import os

import pyarrow.parquet as pq
import pytest
from etl_engine import batch
from etl_engine.cli import app
from etl_engine.executor import run_flow
from typer.testing import CliRunner

from conftest import make_flow


@pytest.fixture
def folder(tmp_path):
    data = tmp_path / "in"
    (data / "sous").mkdir(parents=True)
    (data / "a.csv").write_text("id,dep,nom\n1,31,Toulouse\n2,31,Blagnac\n", encoding="utf-8")
    (data / "b.csv").write_text("id,dep,pop\n3,09,Foix\n", encoding="utf-8")  # une colonne de plus, une de moins
    (data / "sous" / "c.csv").write_text("id,dep,nom\n4,32,Auch\n", encoding="utf-8")
    (data / "notes.txt").write_text("pas un csv", encoding="utf-8")
    return data


def read_out(path):
    return pq.read_table(path)


def test_file_lister_lists_a_folder_with_patterns_and_recursion(registry, folder, tmp_path):
    def run(**params):
        nodes = [
            {"id": "l", "type": "ListFiles", "params": {"folder": str(folder), **params}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "list.parquet")}},
        ]
        res = run_flow(
            make_flow(nodes, [("l", "output", "w", "input")]),
            registry,
            tmp_path / f"run{len(params)}{params.get('recursive', 0)}",
        )
        assert res.status == "done", res.error
        return read_out(tmp_path / "list.parquet").to_pylist()

    flat = run(patterns=["*.csv"])
    assert [r["name"] for r in flat] == ["a.csv", "b.csv"]
    assert flat[0]["stem"] == "a" and flat[0]["ext"] == ".csv" and flat[0]["size"] > 0 and flat[0]["dir"] == str(folder)
    deep = run(patterns=["*.csv"], recursive=True)
    assert [r["name"] for r in deep] == ["a.csv", "b.csv", "c.csv"]
    assert len(run()) == 3  # a.csv, b.csv, notes.txt (le dossier « sous » n'est pas listé)
    assert [r["name"] for r in run(include_dirs=True) if r["is_dir"]] == ["sous"]


def test_file_lister_reads_a_list_file(registry, folder, tmp_path):
    listing = tmp_path / "liste.txt"
    listing.write_text(f"# commentaire\n{folder / 'a.csv'}\n\n{folder / 'absent.csv'}\n", encoding="utf-8")

    def run(missing):
        nodes = [
            {
                "id": "l",
                "type": "ListFiles",
                "params": {"source": "list_file", "list_file": str(listing), "missing": missing},
            },
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / f"l_{missing}.parquet")}},
        ]
        return run_flow(make_flow(nodes, [("l", "output", "w", "input")]), registry, tmp_path / f"run_{missing}")

    assert "fichier introuvable" in run("error").error
    ok = run("skip")
    assert ok.status == "done" and ok.nodes["l"].rows == {"output": 1}


def test_feature_reader_unions_the_schemas_and_copies_attributes(registry, folder, tmp_path):
    nodes = [
        {"id": "l", "type": "ListFiles", "params": {"folder": str(folder), "patterns": ["*.csv"], "recursive": True}},
        {
            "id": "r",
            "type": "ReadListedFiles",
            "params": {"copy_attributes": ["stem"], "sample": str(folder / "a.csv")},
        },
        {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "all.parquet")}},
    ]
    res = run_flow(
        make_flow(nodes, [("l", "output", "r", "input"), ("r", "output", "w", "input")]), registry, tmp_path / "run"
    )
    assert res.status == "done", res.error
    table = read_out(tmp_path / "all.parquet")
    assert table.num_rows == 4
    assert set(table.column_names) >= {"id", "dep", "nom", "pop", "stem"}
    rows = {r["id"]: r for r in table.to_pylist()}
    assert rows["3"]["nom"] is None and rows["1"]["pop"] is None  # attribut absent d'un fichier : vide
    assert rows["4"]["stem"] == "c" and rows["1"]["stem"] == "a"


def test_feature_reader_skips_or_refuses_a_bad_file(registry, folder, tmp_path):
    listing = tmp_path / "liste.csv"
    listing.write_text(
        f"path\n{folder / 'a.csv'}\n{folder / 'notes.txt'}\n", encoding="utf-8"
    )  # .txt est lu comme un CSV
    (folder / "x.bin").write_text("?", encoding="utf-8")
    listing.write_text(f"path\n{folder / 'a.csv'}\n{folder / 'x.bin'}\n", encoding="utf-8")

    def run(on_error):
        nodes = [
            {"id": "l", "type": "ListFiles", "params": {"source": "list_file", "list_file": str(listing)}},
            {"id": "r", "type": "ReadListedFiles", "params": {"on_error": on_error}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / f"o_{on_error}.parquet")}},
        ]
        return run_flow(
            make_flow(nodes, [("l", "output", "r", "input"), ("r", "output", "w", "input")]),
            registry,
            tmp_path / f"r_{on_error}",
        )

    assert run("error").status == "error"
    skipped = run("skip")
    assert skipped.status == "done" and read_out(tmp_path / "o_skip.parquet").num_rows == 2


def test_reader_accepts_a_list_of_paths(registry, folder, tmp_path):
    nodes = [
        {
            "id": "r",
            "type": "Reader",
            "params": {"path": str(folder / "a.csv"), "paths": [str(folder / "b.csv"), str(folder / "sous" / "*.csv")]},
        },
        {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "o.parquet")}},
    ]
    res = run_flow(make_flow(nodes, [("r", "output", "w", "input")]), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert read_out(tmp_path / "o.parquet").num_rows == 4
    missing = make_flow(
        [{"id": "r", "type": "Reader", "params": {"path": str(folder / "a.csv"), "paths": [str(folder / "nope.csv")]}}],
        [],
    )
    assert "fichier introuvable" in run_flow(missing, registry, tmp_path / "run2").error


def test_feature_writer_writes_one_file_per_group(registry, folder, tmp_path):
    out = tmp_path / "out"
    nodes = [
        {"id": "l", "type": "ListFiles", "params": {"folder": str(folder), "patterns": ["*.csv"], "recursive": True}},
        {"id": "r", "type": "ReadListedFiles", "params": {}},
        {"id": "w", "type": "SplitToFiles", "params": {"path": str(out / "dep_{dep}" / "communes.csv")}},
        {"id": "s", "type": "Writer", "params": {"path": str(tmp_path / "summary.parquet")}},
    ]
    links = [("l", "output", "r", "input"), ("r", "output", "w", "input"), ("w", "summary", "s", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert sorted(p.parent.name for p in out.glob("*/communes.csv")) == ["dep_09", "dep_31", "dep_32"]
    assert (out / "dep_31" / "communes.csv").read_text(encoding="utf-8").count("\n") == 3  # en-tête + 2 communes
    summary = {
        r["path"].replace("\\", "/").split("/")[-2]: r["rows"]
        for r in read_out(tmp_path / "summary.parquet").to_pylist()
    }
    assert summary == {"dep_09": 1, "dep_31": 2, "dep_32": 1}


def test_feature_writer_cleans_names_and_refuses_unknown_attributes(registry, communes, tmp_path):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "w", "type": "SplitToFiles", "params": {"path": str(tmp_path / "o" / "{nom}.parquet")}},
    ]
    res = run_flow(make_flow(nodes, [("r", "output", "w", "input")]), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert (tmp_path / "o" / "commune 3.parquet").exists() and len(list((tmp_path / "o").glob("*.parquet"))) == 20
    nodes[1]["params"]["path"] = str(tmp_path / "o2" / "{inconnu}.parquet")
    assert (
        "attribut absent"
        in run_flow(make_flow(nodes, [("r", "output", "w", "input")]), registry, tmp_path / "run2").error
    )


def test_feature_writer_without_placeholder_writes_a_single_file(registry, communes, tmp_path):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "w", "type": "SplitToFiles", "params": {"path": str(tmp_path / "tout.parquet")}},
    ]
    res = run_flow(make_flow(nodes, [("r", "output", "w", "input")]), registry, tmp_path / "run")
    assert res.status == "done" and read_out(tmp_path / "tout.parquet").num_rows == 20


@pytest.fixture
def worker_flow(tmp_path):
    """Un flux paramétré : lit SRC et l'écrit sous DEST."""
    path = tmp_path / "worker.json"
    flow = {
        "name": "worker",
        "published_params": [{"name": "SRC", "required": True}, {"name": "DEST", "default": str(tmp_path / "dest")}],
        "nodes": [
            {"id": "r", "type": "Reader", "params": {"path": "${SRC}"}},
            {
                "id": "w",
                "type": "Writer",
                "params": {"path": "${DEST}/${sys:flow_name}_{stem}.parquet".replace("{stem}", "out")},
            },
        ],
        "links": [{"id": "l", "from": ["r", "output"], "to": ["w", "input"]}],
    }
    path.write_text(json.dumps(flow), encoding="utf-8")
    return path


def test_flow_runner_runs_a_flow_per_file_in_parallel(registry, folder, worker_flow, tmp_path):
    dest = tmp_path / "dest"
    nodes = [
        {"id": "l", "type": "ListFiles", "params": {"folder": str(folder), "patterns": ["*.csv", "*.txt"]}},
        {
            "id": "f",
            "type": "RunFlow",
            "params": {
                "flow": str(worker_flow),
                "parallel": 2,
                "parameters": [{"name": "SRC", "attribute": "path"}, {"name": "DEST", "value": str(dest)}],
            },
        },
        {"id": "ok", "type": "Writer", "params": {"path": str(tmp_path / "ok.parquet")}},
        {"id": "ko", "type": "Writer", "params": {"path": str(tmp_path / "ko.parquet")}},
    ]
    links = [("l", "output", "f", "input"), ("f", "Succeeded", "ok", "input"), ("f", "Failed", "ko", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run")
    assert res.status == "done", res.error
    ok = read_out(tmp_path / "ok.parquet").to_pylist()
    assert sorted(r["name"] for r in ok) == [
        "a.csv",
        "b.csv",
        "notes.txt",
    ]  # notes.txt se lit comme un CSV d'une colonne
    assert not (tmp_path / "ko.parquet").exists() and all(r["_flow_status"] == "done" for r in ok)
    assert (dest / "worker_out.parquet").exists()


def test_flow_runner_reports_failures_and_checks_parameters(registry, folder, worker_flow, tmp_path):
    def flow(params, files=("a.csv",)):
        listing = tmp_path / "liste.txt"
        listing.write_text("\n".join(str(folder / f) for f in files), encoding="utf-8")
        nodes = [
            {"id": "l", "type": "ListFiles", "params": {"source": "list_file", "list_file": str(listing)}},
            {"id": "f", "type": "RunFlow", "params": {"flow": str(worker_flow), "parameters": params}},
            {"id": "ko", "type": "Writer", "params": {"path": str(tmp_path / "ko2.parquet")}},
        ]
        return make_flow(nodes, [("l", "output", "f", "input"), ("f", "Failed", "ko", "input")])

    assert (
        "paramètres inconnus du flux lancé"
        in run_flow(flow([{"name": "NOPE", "value": "1"}]), registry, tmp_path / "r1").error
    )
    # un fichier que le flux lancé ne sait pas lire le fait échouer, pas le flux appelant : l'entité sort sur « Failed »
    broken = tmp_path / "broken.bin"
    broken.write_text("?", encoding="utf-8")
    res = run_flow(
        flow([{"name": "SRC", "attribute": "path"}], files=(str(folder / "a.csv"), str(broken))),
        registry,
        tmp_path / "r3",
    )
    assert res.status == "done", res.error
    assert res.nodes["f"].rows == {"Succeeded": 1, "Failed": 1}
    failed = read_out(tmp_path / "ko2.parquet").to_pylist()
    assert len(failed) == 1 and failed[0]["name"] == "broken.bin" and failed[0]["_flow_status"] == "error"
    assert failed[0]["_flow_error"]


def test_batch_helpers(tmp_path, folder):
    assert [i.label for i in batch.items_from_values("SRC", batch.expand_each(str(folder / "*.csv")))] == [
        "a.csv",
        "b.csv",
    ]
    listing = tmp_path / "l.txt"
    listing.write_text("# x\nuno\n\ndos\n", encoding="utf-8")
    assert [i.params["SRC"] for i in batch.items_from_list("SRC", listing)] == ["uno", "dos"]
    csvf = tmp_path / "p.csv"
    csvf.write_text("SRC;ANNEE\nZ:/a.shp;2024\nZ:/b.shp;2025\n", encoding="utf-8")
    items = batch.items_from_csv(csvf)
    assert [i.params for i in items] == [{"SRC": "Z:/a.shp", "ANNEE": "2024"}, {"SRC": "Z:/b.shp", "ANNEE": "2025"}]


def test_each_runs_a_flow_per_file(folder, worker_flow, tmp_path):
    dest = tmp_path / "dest_each"
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(worker_flow),
            "--each",
            str(folder / "*.csv"),
            "--as",
            "SRC",
            "--parallel",
            "2",
            "-p",
            f"DEST={dest}",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "2 run(s)" in result.stdout and "Lot terminé : 2 réussi(s), 0 en échec" in result.stdout
    assert (dest / "worker_out.parquet").exists()


def test_each_exit_code_and_usage_errors(folder, worker_flow, tmp_path):
    bad = tmp_path / "bad.bin"  # format non reconnu : le flux lancé échoue
    bad.write_text("?", encoding="utf-8")
    failing = CliRunner().invoke(app, ["run", str(worker_flow), "--each", str(bad), "--as", "SRC"])
    assert failing.exit_code == 1 and "1 en échec" in failing.stdout
    assert (
        CliRunner().invoke(app, ["run", str(worker_flow), "--each", str(folder / "*.csv")]).exit_code == 3
    )  # --as manque
    assert (
        CliRunner().invoke(app, ["run", str(worker_flow), "--each", str(folder / "*.csv"), "--as", "NOPE"]).exit_code
        == 3
    )
    assert (
        CliRunner().invoke(app, ["run", str(worker_flow), "--each", str(folder / "*.zzz"), "--as", "SRC"]).exit_code
        == 3
    )


def test_each_csv_gives_one_run_per_row(folder, worker_flow, tmp_path):
    params = tmp_path / "params.csv"
    params.write_text(
        f"SRC;DEST\n{folder / 'a.csv'};{tmp_path / 'd1'}\n{folder / 'b.csv'};{tmp_path / 'd2'}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["run", str(worker_flow), "--each-csv", str(params)])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "d1" / "worker_out.parquet").exists() and (tmp_path / "d2" / "worker_out.parquet").exists()


def test_parallel_batch_gives_each_run_its_share_of_the_machine(monkeypatch):
    seen = []

    class Proc:
        returncode = 0
        stdout = type("S", (), {"read": staticmethod(lambda: b"")})()

        def poll(self):
            return 0

    monkeypatch.setattr(batch.subprocess, "Popen", lambda cmd, **kw: seen.append(kw["env"]) or Proc())
    monkeypatch.setattr(batch.duck, "machine_budget_mb", lambda: 8000)
    monkeypatch.setattr(batch.duck, "machine_threads", lambda: 12)
    items = [batch.BatchItem({"A": "1"}, "x"), batch.BatchItem({"A": "2"}, "y")]
    batch.run_batch("f.json", items, parallel=2)
    assert {(e["ETL_RUN_MEMORY_MB"], e["ETL_RUN_THREADS"]) for e in seen} == {("4000", "6")}
    seen.clear()
    batch.run_batch("f.json", items[:1], parallel=1)
    assert "ETL_RUN_MEMORY_MB" not in seen[0] or seen[0].get("ETL_RUN_MEMORY_MB") == os.environ.get("ETL_RUN_MEMORY_MB")
