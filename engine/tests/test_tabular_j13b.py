import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import shapely
from etl_engine.analysis import analyze
from etl_engine.data import GEOM, get_crs
from etl_engine.executor import run_flow
from etl_engine.modifiers.build_list import BuildList
from etl_engine.modifiers.create_features import CreateFeatures
from etl_engine.modifiers.execute_sql import ExecuteSql
from etl_engine.modifiers.http_request import HttpRequest
from etl_engine.modifiers.python_script import PythonScript
from etl_engine.modifiers.sample import Sample
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier, table

from conftest import make_flow


def rows(t):
    return t.to_pylist()


# ------------------------------------------------------------------ BuildList
DATA = table(dep=["31", "31", "09", "31", "09"], nom=["b", "a", "z", "a", None])


def test_list_builder_groups_in_arrival_order():
    out = run_modifier(BuildList, {"group_by": ["dep"], "attribute": "nom"}, DATA)["output"]
    assert rows(out) == [{"dep": "31", "nom_liste": ["b", "a", "a"]}, {"dep": "09", "nom_liste": ["z"]}]


def test_list_builder_distinct_sorted_and_single_group():
    out = run_modifier(BuildList, {"attribute": "nom", "distinct": True, "sort": True, "list_name": "noms"}, DATA)[
        "output"
    ]
    assert rows(out) == [{"noms": ["a", "b", "z"]}]


def test_list_builder_keeps_the_first_geometry():
    pts = points([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], g=["a", "a", "b"], v=[1, 2, 3])
    out = run_modifier(BuildList, {"group_by": ["g"], "attribute": "v", "geometry": "first"}, pts)["output"]
    assert (
        rows(out)[0]["v_liste"] == [1, 2]
        and shapely.from_wkb(rows(out)[0][GEOM]).x == 1.0
        and get_crs(out) == "EPSG:2154"
    )


# ------------------------------------------------------------------ Sample
NUMBERS = table(n=list(range(10)))


def test_sampler_modes():
    first = run_modifier(Sample, {"mode": "first", "count": 3}, NUMBERS)
    assert [r["n"] for r in rows(first["Sampled"])] == [0, 1, 2] and first["Remaining"].num_rows == 7
    last = run_modifier(Sample, {"mode": "last", "count": 2}, NUMBERS)
    assert [r["n"] for r in rows(last["Sampled"])] == [8, 9]
    every = run_modifier(Sample, {"mode": "every", "count": 4}, NUMBERS)
    assert [r["n"] for r in rows(every["Sampled"])] == [0, 4, 8]  # même résultat quel que soit le découpage en lots
    assert [r["n"] for r in rows(every["Remaining"])] == [1, 2, 3, 5, 6, 7, 9]


def test_sampler_random_is_reproducible_and_exact():
    a = run_modifier(Sample, {"mode": "random", "count": 4, "seed": 7}, NUMBERS)["Sampled"]
    b = run_modifier(Sample, {"mode": "random", "count": 4, "seed": 7}, NUMBERS)["Sampled"]
    other = run_modifier(Sample, {"mode": "random", "count": 4, "seed": 8}, NUMBERS)["Sampled"]
    assert a.num_rows == 4 and rows(a) == rows(b) and rows(a) != rows(other)
    assert [r["n"] for r in rows(a)] == sorted(r["n"] for r in rows(a))  # ordre d'arrivée conservé
    assert run_modifier(Sample, {"mode": "random", "count": 50}, NUMBERS)["Remaining"].num_rows == 0


# ------------------------------------------------------------------ ExecuteSql
def test_inline_querier_joins_several_inputs():
    params = {
        "inputs": ["a", "b"],
        "sql": "SELECT a.code, a.nom, b.pop FROM a LEFT JOIN b ON a.code = b.code ORDER BY a.code",
    }
    a, b = table(code=[1, 2, 3], nom=["x", "y", "z"]), table(code=[1, 3], pop=[10, 30])
    out = run_modifier(ExecuteSql, params, {"a": a, "b": b})["output"]
    assert rows(out) == [
        {"code": 1, "nom": "x", "pop": 10},
        {"code": 2, "nom": "y", "pop": None},
        {"code": 3, "nom": "z", "pop": 30},
    ]
    assert ExecuteSql.ports(ExecuteSql.Params.model_validate(params)) == (("a", "b"), ("output",))


def test_inline_querier_handles_geometry_and_keeps_the_crs():
    pts = points([0.0, 10.0], [0.0, 0.0], nom=["a", "b"])
    sql = "SELECT nom, ST_X(geometry) AS x, ST_Buffer(geometry, 1.0) AS geometry FROM input WHERE ST_X(geometry) > 5"
    out = run_modifier(ExecuteSql, {"sql": sql}, pts)["output"]
    assert rows(out)[0]["nom"] == "b" and rows(out)[0]["x"] == 10.0
    assert round(shapely.from_wkb(rows(out)[0][GEOM]).area, 1) == 3.1 and get_crs(out) == "EPSG:2154"


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("", "vide"),
        ("DROP TABLE input", "SELECT"),
        ("SELECT 1; SELECT 2", "SELECT"),
        ("SELECT * FROM read_csv('/etc/passwd')", "invalide|échec"),
        ("SELECT inconnu FROM input", "invalide"),
    ],
)
def test_inline_querier_refuses_unsafe_or_wrong_queries(sql, message):
    with pytest.raises(ConfigError, match=message):
        run_modifier(ExecuteSql, {"sql": sql}, table(a=[1]))


def test_inline_querier_schema_for_the_editor(registry):
    flow = make_flow(
        [
            {"id": "c", "type": "CreateFeatures", "params": {"attributes": [{"name": "v", "expression": "1"}]}},
            {"id": "q", "type": "ExecuteSql", "params": {"sql": "SELECT v, v * 2 AS double FROM input"}},
        ],
        [("c", "output", "q", "input")],
    )
    result = analyze(flow, registry)
    assert [c["name"] for c in result["schemas"]["q/output"]["fields"]] == ["v", "double"]


# ------------------------------------------------------------------ CreateFeatures
def test_creator_makes_features_with_index_and_geometry():
    out = run_modifier(
        CreateFeatures,
        {
            "count": 5,
            "attributes": [{"name": "n", "expression": '"_index" + 1'}, {"name": "carre", "expression": '"n" * "n"'}],
            "geometry": "point",
            "x": '600000 + "_index"',
            "y": "6500000",
            "crs": "EPSG:2154",
        },
        batch_rows=2,
    )["output"]
    assert out.column("n").to_pylist() == [1, 2, 3, 4, 5] and out.column("carre").to_pylist() == [1, 4, 9, 16, 25]
    assert [shapely.from_wkb(w).x for w in out.column(GEOM).to_pylist()] == [600000.0 + i for i in range(5)]
    assert get_crs(out) == "EPSG:2154"


def test_creator_wkt_and_validation():
    out = run_modifier(CreateFeatures, {"geometry": "wkt", "wkt": "POLYGON((0 0, 1 0, 1 1, 0 0))"})["output"]
    assert shapely.from_wkb(out.column(GEOM)[0].as_py()).area == 0.5
    with pytest.raises(ConfigError, match="WKT invalide"):
        run_modifier(CreateFeatures, {"geometry": "wkt", "wkt": "pas du wkt"})
    with pytest.raises(ConfigError, match="réservé"):
        run_modifier(CreateFeatures, {"attributes": [{"name": "geometry", "expression": "1"}]})


# ------------------------------------------------------------------ PythonScript
def test_python_caller_transforms_batches_and_keeps_state():
    code = """
def process(table, ctx):
    ctx.state["seen"] = ctx.state.get("seen", 0) + table.num_rows
    doubled = pc.multiply(table.column("n"), 2)
    ctx.info(f"{ctx.state['seen']} lignes")
    return table.append_column("double", doubled).append_column("seen", pa.array([ctx.state["seen"]] * table.num_rows))
"""
    out = run_modifier(PythonScript, {"code": code}, table(n=[1, 2, 3, 4]), batch_rows=2)["output"]
    assert out.column("double").to_pylist() == [2, 4, 6, 8] and out.column("seen").to_pylist() == [2, 2, 4, 4]


def test_python_caller_outputs_and_errors():
    code = """
def process(table, ctx):
    ok = pc.greater(table.column("n"), 1)
    return {"output": table.filter(ok), "Rejected": reject(table.filter(pc.invert(ok)), "trop petit")}
"""
    out = run_modifier(PythonScript, {"code": code}, table(n=[1, 2, 3]))
    assert out["output"].column("n").to_pylist() == [2, 3] and out["Rejected"].column(
        "_rejection_reason"
    ).to_pylist() == ["trop petit"]
    with pytest.raises(ConfigError, match="syntaxe ligne"):
        run_modifier(PythonScript, {"code": "def process(table, ctx:\n  return table"}, table(n=[1]))
    with pytest.raises(ConfigError, match="process"):
        run_modifier(PythonScript, {"code": "x = 1"}, table(n=[1]))
    with pytest.raises(RuntimeError, match=r"ZeroDivisionError.*ligne 3"):
        run_modifier(
            PythonScript, {"code": "def process(table, ctx):\n    # un commentaire\n    return 1 / 0"}, table(n=[1])
        )
    with pytest.raises(ConfigError, match="sortie inconnue"):
        run_modifier(PythonScript, {"code": 'def process(table, ctx):\n    return {"autre": table}'}, table(n=[1]))


def test_python_caller_does_not_run_code_during_analysis(registry, tmp_path):
    marker = tmp_path / "execute.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\ndef process(table, ctx):\n    return table"
    flow = make_flow([{"id": "p", "type": "PythonScript", "params": {"code": code}}], [])
    analyze(flow, registry)
    assert not marker.exists()  # l'analyse compile, elle n'exécute jamais le code de l'utilisateur
    assert any(
        "syntaxe" in i["message"]
        for i in analyze(make_flow([{"id": "p", "type": "PythonScript", "params": {"code": "def ("}}], []), registry)[
            "issues"
        ]
    )


# ------------------------------------------------------------------ HttpRequest
class Handler(BaseHTTPRequestHandler):
    hits: list[str] = []
    flaky = {"n": 0}

    def _send(self, code: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        Handler.hits.append(self.path)
        if self.path.startswith("/flaky"):
            Handler.flaky["n"] += 1
            return self._send(503 if Handler.flaky["n"] < 2 else 200, {"try": Handler.flaky["n"]})
        if self.path.startswith("/missing"):
            return self._send(404, {"error": "absent"})
        self._send(200, {"path": self.path, "auth": self.headers.get("X-Key")})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self._send(200, {"echo": self.rfile.read(length).decode()})

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def server():
    Handler.hits, Handler.flaky["n"] = [], 0
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_http_caller_fills_the_url_and_headers_from_attributes(server):
    params = {"url": server + "/q?mot={mot}", "headers": [{"name": "X-Key", "value": "k-{id}"}], "concurrency": 3}
    out = run_modifier(HttpRequest, params, table(id=[1, 2, 3], mot=["a b", "é&", None]))["output"]
    bodies = [json.loads(r["_http_body"]) for r in rows(out)]
    assert sorted(b["path"] for b in bodies) == ["/q?mot=", "/q?mot=%C3%A9%26", "/q?mot=a%20b"]
    assert sorted(b["auth"] for b in bodies) == ["k-1", "k-2", "k-3"] and {r["_http_status"] for r in rows(out)} == {
        200
    }


def test_http_caller_posts_retries_and_reports_failures(server):
    posted = run_modifier(HttpRequest, {"url": server + "/x", "method": "POST", "body": '{"id": {id}}'}, table(id=[7]))[
        "output"
    ]
    assert json.loads(json.loads(rows(posted)[0]["_http_body"])["echo"]) == {"id": 7}
    flaky = run_modifier(HttpRequest, {"url": server + "/flaky", "retries": 2}, table(id=[1]))
    assert flaky["output"].num_rows == 1 and Handler.flaky["n"] == 2  # un 503 puis un 200
    missing = run_modifier(HttpRequest, {"url": server + "/missing"}, table(id=[1]))
    assert missing["Rejected"].num_rows == 1 and rows(missing["Rejected"])[0]["_rejection_reason"] == "HTTP 404"
    refused = run_modifier(HttpRequest, {"url": "http://127.0.0.1:9/x", "retries": 0, "timeout": 2}, table(id=[1]))
    assert refused["Rejected"].num_rows == 1 and rows(refused["Rejected"])[0]["_http_status"] is None


def test_http_caller_validates_its_configuration():
    with pytest.raises(ConfigError, match="http://"):
        run_modifier(HttpRequest, {"url": "file:///etc/passwd"}, table(id=[1]))
    with pytest.raises(ConfigError, match="attributs? absents?"):
        run_modifier(HttpRequest, {"url": "http://localhost/{nope}"}, table(id=[1]))


def test_http_caller_in_a_flow(server, registry, tmp_path):
    flow = make_flow(
        [
            {
                "id": "c",
                "type": "CreateFeatures",
                "params": {"count": 2, "attributes": [{"name": "code", "expression": '"_index"'}]},
            },
            {"id": "h", "type": "HttpRequest", "params": {"url": server + "/c/{code}"}},
            {
                "id": "j",
                "type": "ExtractJson",
                "params": {"attribute": "_http_body", "fields": [{"path": "path", "name": "chemin"}]},
            },
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "o.csv")}},
        ],
        [("c", "output", "h", "input"), ("h", "output", "j", "input"), ("j", "output", "w", "input")],
    )
    assert run_flow(flow, registry, tmp_path / "run").status == "done"
    text = (tmp_path / "o.csv").read_text(encoding="utf-8")
    assert "/c/0" in text and "/c/1" in text
