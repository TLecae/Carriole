"""Ports définis par leur requête, calculés à la demande (ADR 0044) : mêmes résultats que la voie par lots."""

import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.cache import define, has_port, materialize, port_path, sql_path
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow
from etl_engine.sqlroute import same_types

from conftest import make_flow

N = 12


@pytest.fixture
def source(tmp_path):
    """Points avec des valeurs nulles, des textes numériques ou non, une géométrie absente."""
    points = shapely.points(range(N), range(N))
    geoms = [None if i == 5 else g for i, g in enumerate(shapely.to_wkb(points))]
    table = pa.table(
        {
            "id": pa.array(range(N), pa.int64()),
            "pop": pa.array([None if i == 3 else i * 100 for i in range(N)], pa.int64()),
            "txt": pa.array([str(i) if i % 4 else f"x{i}" for i in range(N)]),
            "dep": pa.array(["31" if i % 2 else "09" for i in range(N)]),
            "js": pa.array(
                [
                    None if i == 2 else "pas du json" if i == 7 else f'{{"a": {i}, "b": {{"c": "v{i}"}}}}'
                    for i in range(N)
                ]
            ),
            GEOM: pa.array(geoms, pa.binary()),
        }
    )
    path = tmp_path / "in.parquet"
    pq.write_table(with_crs(table, "EPSG:2154"), path)
    return path


def chain(source, *nodes):
    """Lecture → nœuds à la suite (premier port de sortie de chacun)."""
    all_nodes = [{"id": "r", "type": "Reader", "params": {"path": str(source)}}, *nodes]
    links = []
    for up, down in zip(all_nodes, all_nodes[1:], strict=False):
        links.append((up["id"], up.get("port", "output"), down["id"], "input"))
    return make_flow([{k: v for k, v in n.items() if k != "port"} for n in all_nodes], links)


def both(registry, tmp_path, flow, node, ports):
    """Ports de `node` par la voie différée et par la voie par lots : (différée, par lots, résultat différé)."""
    lazy = run_flow(flow, registry, tmp_path / "lazy")
    batch = run_flow(flow, registry, tmp_path / "batch", sql_route=False)
    assert lazy.status == batch.status == "done", (lazy.error, batch.error)
    out = {}
    for port in ports:
        assert sql_path(port_path(tmp_path / "lazy", node, port)).is_file(), f"{node}/{port} n'est pas différé"
        out[port] = (
            pq.read_table(materialize(tmp_path / "lazy", node, port)),
            pq.read_table(port_path(tmp_path / "batch", node, port)),
        )
    assert lazy.nodes[node].rows == batch.nodes[node].rows
    return out, lazy


def same(a: pa.Table, b: pa.Table):
    assert same_types(a.schema, b.schema), (a.schema, b.schema)
    assert get_crs(a) == get_crs(b)
    assert a.to_pylist() == b.to_pylist()


FILTER, ADD = "ExtractByExpression", "AddAttributes"
ATTRIBUTES = [
    {"name": "p2", "expression": '"pop" * 2'},
    {"name": "t", "expression": "\"p2\" || ''", "type": "text"},
    {"name": "pop", "expression": 'coalesce("pop", -1)'},
    {"name": "x", "expression": "ST_X(geometry)"},
]
SWAP = [{"source": "txt", "target": "dep"}, {"source": "dep", "target": "txt"}]
ROUTES = [{"output": "A", "condition": '"pop" >= 500'}, {"output": "B", "condition": "\"dep\" = '31'"}]
RULES = [
    {"attribute": "k", "action": "set", "expression": '"pop" / 100'},
    {"attribute": "dep", "action": "rename", "new_name": "departement"},
    {"attribute": "txt", "action": "remove"},
]
INT_TXT = {"attribute": "txt", "type": "integer"}
CASTS = [{"attribute": "pop", "type": "text"}, {"attribute": "id", "type": "float"}]
CASES = {
    "filtre": (FILTER, {"conditions": ['"pop" >= 300', "\"dep\" = '31'"]}, ["Passed", "Failed"]),
    "filtre spatial OR": (FILTER, {"conditions": ['"pop" > 900', "ST_X(geometry) < 2"], "combine": "OR"}, ["Passed"]),
    "attributs": (ADD, {"attributes": ATTRIBUTES}, ["output"]),
    "attributs (échec vide)": (
        ADD,
        {"attributes": [{"name": "n", "expression": '"txt"', "type": "integer"}], "on_error": "null"},
        ["output"],
    ),
    "retirer": ("DropAttributes", {"attributes": ["txt"], "remove_geometry": True}, ["output"]),
    "garder": ("RetainAttributes", {"attributes": ["dep", "id"]}, ["output"]),
    "renommer (échange)": ("RenameAttributes", {"renamings": SWAP}, ["output"]),
    "plusieurs sorties (first)": ("RouteByExpression", {"tests": ROUTES, "mode": "first"}, ["A", "B", "Failed"]),
    "plusieurs sorties (all)": ("RouteByExpression", {"tests": ROUTES, "mode": "all"}, ["A", "B", "Failed"]),
    "gérer": ("RefactorAttributes", {"rules": RULES}, ["output"]),
    "convertir": ("CastAttributes", {"conversions": CASTS}, ["output"]),
    "convertir (null)": ("CastAttributes", {"conversions": [INT_TXT], "on_error": "null"}, ["output"]),
    "renommer en masse": ("RenameAttributesByPattern", {"prefix": "a_", "case": "upper"}, ["output"]),
    "remplacer du texte": ("ReplaceText", {"attributes": ["txt", "dep"], "find": "1", "replace": "un"}, ["output"]),
    "remplacer (regex, casse)": (
        "ReplaceText",
        {"attributes": ["txt"], "find": "X(\\d)", "replace": "y\\1", "regex": True, "ignore_case": True},
        ["output"],
    ),
    "remplacer (valeur entière)": (
        "ReplaceText",
        {"attributes": ["dep"], "find": "31", "replace": "Haute-Garonne", "whole_value": True, "suffix": "_nom"},
        ["output"],
    ),
    "compteur": ("Counter", {"attribute": "n", "start": 10, "step": 5}, ["output"]),
    "compteur (remplace)": ("Counter", {"attribute": "pop"}, ["output"]),
    "valeurs vides (remplir)": ("ReplaceNulls", {"attributes": ["js"], "mode": "fill", "value": "{}"}, ["output"]),
    "valeurs vides (vider)": ("ReplaceNulls", {"attributes": ["txt"], "mode": "to_null", "value": "x4"}, ["output"]),
    "doublons": ("FindDuplicates", {"keys": ["dep"]}, ["Unique", "Duplicate"]),
    "doublons (tous les attributs)": ("FindDuplicates", {}, ["Unique", "Duplicate"]),
    "json": (
        "ExtractJson",
        {"attribute": "js", "fields": [{"path": "a", "name": "a", "type": "integer"}, {"path": "b.c", "name": "c"}]},
        ["output"],
    ),
    "correspondance": (
        "MapValues",
        {"attribute": "dep", "destination": "nom", "mappings": [{"source": "31", "target": "HG"}], "unmatched": "keep"},
        ["output"],
    ),
    "correspondance (casse, défaut)": (
        "MapValues",
        {
            "attribute": "txt",
            "mappings": [{"source": "X4", "target": "quatre"}, {"source": "x4", "target": "QUATRE"}],
            "unmatched": "default",
            "default": "?",
            "ignore_case": True,
        },
        ["output"],
    ),
}


@pytest.mark.parametrize("keep_empty", [False, True])
def test_explode_list_same_result_as_the_batch_route(registry, source, tmp_path, keep_empty):
    split = {"id": "s", "type": "SplitText", "params": {"attribute": "js", "separator": ",", "mode": "list"}}
    explode = {"attribute": "js_list", "element": "morceau", "index": "i", "keep_empty": keep_empty}
    flow = chain(source, split, {"id": "n", "type": "ExplodeList", "params": explode})
    out, _ = both(registry, tmp_path, flow, "n", ["output"])
    same(*out["output"])


@pytest.mark.parametrize("case", CASES)
def test_same_result_as_the_batch_route(registry, source, tmp_path, case):
    kind, params, ports = CASES[case]
    out, _ = both(registry, tmp_path, chain(source, {"id": "n", "type": kind, "params": params}), "n", ports)
    for lazy, batch in out.values():
        same(lazy, batch)
        # la géométrie garde son SCR par les deux voies (renommer le perdait : plus de carte, plus de SCR à l'export)
        assert GEOM not in lazy.column_names or get_crs(lazy) == "EPSG:2154"


def test_a_chain_is_one_query_read_by_a_batch_node(registry, source, tmp_path):
    flow = chain(
        source,
        {"id": "f", "type": "ExtractByExpression", "params": {"conditions": ['"pop" >= 200']}, "port": "Passed"},
        {"id": "a", "type": "AddAttributes", "params": {"attributes": [{"name": "k", "expression": '"pop" / 100'}]}},
        {"id": "n", "type": "RenameAttributes", "params": {"renamings": [{"source": "k", "target": "kilo"}]}},
        {"id": "b", "type": "Buffer", "params": {"distance": 1}},  # voie par lots : lit la requête composée
    )
    out, lazy = both(registry, tmp_path, flow, "n", ["output"])
    same(*out["output"])
    assert not port_path(tmp_path / "lazy", "f", "Passed").exists()  # rien d'écrit en amont
    batch = pq.read_table(port_path(tmp_path / "batch", "b", "output"))
    assert pq.read_table(port_path(tmp_path / "lazy", "b", "output")).to_pylist() == batch.to_pylist()
    assert lazy.nodes["b"].rows_in == {"input": 9}


def test_a_data_error_goes_to_rejected_by_the_batch_route(registry, source, tmp_path):
    """« x0 », « x4 »… ne se convertissent pas : le nœud passe par la voie par lots, qui les rejette avec le motif."""
    node = {
        "id": "a",
        "type": "AddAttributes",
        "params": {"attributes": [{"name": "n", "expression": '"txt"', "type": "integer"}]},
    }
    filt = {"id": "f", "type": FILTER, "params": {"conditions": ['CAST("txt" AS INTEGER) > 2']}}
    cast = {"id": "c", "type": "CastAttributes", "params": {"conversions": [INT_TXT]}}
    for n in (node, filt, cast):
        res = run_flow(chain(source, n), registry, tmp_path / n["id"])
        assert res.status == "done", res.error
        assert not sql_path(port_path(tmp_path / n["id"], n["id"], "Rejected")).exists()
        rejected = pq.read_table(port_path(tmp_path / n["id"], n["id"], "Rejected"))
        assert rejected.column("id").to_pylist() == [0, 4, 8] and res.nodes[n["id"]].rows["Rejected"] == 3


def test_try_keeps_a_chain_exact_whatever_the_evaluation_order(registry, source, tmp_path):
    """Le second filtre ne vaut que sur les lignes que le premier garde (textes numériques) : DuckDB peut l'évaluer
    avant, sur toutes les lignes ; `TRY` y rend NULL au lieu d'une erreur."""
    flow = chain(
        source,
        {
            "id": "f",
            "type": "ExtractByExpression",
            "params": {"conditions": ["\"txt\" NOT LIKE 'x%'"]},
            "port": "Passed",
        },
        {"id": "g", "type": "ExtractByExpression", "params": {"conditions": ['CAST("txt" AS INTEGER) > 5']}},
    )
    out, _ = both(registry, tmp_path, flow, "g", ["Passed", "Failed"])
    same(*out["Passed"])
    assert out["Passed"][0].column("id").to_pylist() == [6, 7, 9, 10, 11]


def test_a_non_deterministic_expression_is_written(registry, source, tmp_path):
    node = {"id": "a", "type": "AddAttributes", "params": {"attributes": [{"name": "r", "expression": "random()"}]}}
    res = run_flow(chain(source, node), registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert port_path(tmp_path / "run", "a", "output").is_file()


def test_without_cache_a_final_deferred_node_keeps_its_chain(registry, source, tmp_path):
    flow = chain(
        source,
        {"id": "f", "type": "ExtractByExpression", "params": {"conditions": ['"pop" >= 200']}, "port": "Passed"},
        {"id": "b", "type": "Buffer", "params": {"distance": 1}},  # écrit son port : f n'est plus désigné par rien
        {"id": "a", "type": "AddAttributes", "params": {"attributes": [{"name": "k", "expression": "1"}]}},
    )
    res = run_flow(flow, registry, tmp_path / "run", cache=False)
    assert res.status == "done", res.error
    assert not (tmp_path / "run" / "f").exists()  # lu par le tampon, puis libéré
    assert has_port(port_path(tmp_path / "run", "b", "output"))  # désigné par la définition de « a »
    assert pq.read_table(materialize(tmp_path / "run", "a", "output")).num_rows == 8  # sans géométrie : rejetée


def test_deferred_ports_are_reused_by_the_next_run(registry, source, tmp_path):
    first = tmp_path / "run1"
    flow = chain(
        source,
        {"id": "f", "type": "ExtractByExpression", "params": {"conditions": ['"pop" >= 200']}, "port": "Passed"},
        {"id": "a", "type": "AddAttributes", "params": {"attributes": [{"name": "k", "expression": "1"}]}},
    )
    assert run_flow(flow, registry, first).status == "done"
    flow.node("a").params["attributes"][0]["expression"] = "2"
    res = run_flow(flow, registry, tmp_path / "run2", reuse_from=first)
    assert res.nodes["f"].status == "cached" and res.nodes["a"].status == "done"
    assert set(pq.read_table(materialize(tmp_path / "run2", "a", "output")).column("k").to_pylist()) == {2}


def test_a_heavy_port_only_defined_is_written_once_read(registry, source, tmp_path):
    """Statistiques lancées seules (rien ne lit leurs entités : port seulement défini), puis un nœud branché en aval :
    le run suivant ne reprend pas la définition, que chaque lecture recalculerait (tri et jointure de toute la table),
    mais écrit le port une fois."""
    stats = {"id": "s", "type": "StatisticsByGroup", "params": {"group_by": ["dep"], "attributes": [], "functions": []}}
    first = tmp_path / "run1"
    assert run_flow(chain(source, stats), registry, first).status == "done"
    assert sql_path(port_path(first, "s", "output")).is_file()

    flow = chain(
        source,
        stats,
        {"id": "a", "type": "AddAttributes", "params": {"attributes": [{"name": "k", "expression": '"_count" * 2'}]}},
        {"id": "b", "type": "Buffer", "params": {"distance": 1}},
    )
    res = run_flow(flow, registry, tmp_path / "run2", reuse_from=first)
    assert res.status == "done", res.error
    assert res.nodes["s"].status == "done"
    assert port_path(tmp_path / "run2", "s", "output").is_file()
    assert pq.read_table(port_path(tmp_path / "run2", "b", "output")).column("k").to_pylist() == [12] * (N - 1)


def test_expressions_reach_no_other_file(registry, source, tmp_path):
    """Composées dans une requête qui lit les ports, les expressions d'utilisateur n'atteignent pas d'autre fichier."""
    secret = tmp_path / "secret.csv"
    secret.write_text("x\n1\n", encoding="utf-8")
    read = f"(SELECT count(*) FROM read_csv('{secret.as_posix()}')) > 0"
    flow = chain(source, {"id": "f", "type": FILTER, "params": {"conditions": [read]}})
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "error" and "secret.csv" in (res.error or "")


def test_computing_a_definition_reaches_no_other_file(tmp_path):
    """Une définition (fichier du run) ne lit que les ports qu'elle désigne, même modifiée à la main."""
    secret = tmp_path / "secret.csv"
    secret.write_text("x\n1\n", encoding="utf-8")
    define(port_path(tmp_path / "run", "n", "output"), f"SELECT * FROM read_csv('{secret.as_posix()}')", {}, None)
    with pytest.raises(duckdb.PermissionException):
        materialize(tmp_path / "run", "n", "output")


def test_an_abandoned_computation_stops_without_writing_anything(tmp_path):
    """Un calcul que plus personne n'attend (inspecteur : un autre port affiché) s'arrête, sans rien écrire."""
    slow = "SELECT count(*) AS n FROM range(200000) a, range(200000) b WHERE (a.range * 7 + b.range) % 1000003 = 1"
    define(port_path(tmp_path / "run", "n", "output"), slow, {}, None)
    start = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        materialize(tmp_path / "run", "n", "output", abandoned=lambda: True)
    assert time.monotonic() - start < 10
    assert [p.name for p in (tmp_path / "run" / "n").iterdir()] == ["output.sql.json"]
