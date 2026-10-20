import base64
import json
import sys
import threading
import time

import mapbox_vector_tile as mvt
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_api import data_service as ds
from etl_api.main import create_app
from etl_engine.data import GEOM, with_crs
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import Settings
from fastapi.testclient import TestClient

N = 50
RUN = "run0001"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """Un vrai cache : un run exécuté par le moteur."""
    tmp = tmp_path_factory.mktemp("insp")
    xs = np.linspace(600_000, 610_000, N)
    t = pa.table(
        {
            "id": pa.array(range(N), pa.int64()),
            "nom": pa.array([f"commune {i}" if i % 10 else None for i in range(N)]),
            "pop": pa.array([i * 100 for i in range(N)], pa.int64()),
            "dep": pa.array(["31" if i % 2 == 0 else "09" for i in range(N)]),
            GEOM: pa.array(shapely.to_wkb(shapely.points(xs, np.full(N, 6.5e6))), pa.binary()),
        }
    )
    src = tmp / "in.parquet"
    pq.write_table(with_crs(t, "EPSG:2154"), src)
    flow = Flow.model_validate(
        {
            "nodes": [
                {"id": "r", "type": "Reader", "params": {"path": str(src)}},
                {"id": "t", "type": "ExtractByExpression", "params": {"conditions": ['"pop" >= 1000']}},
            ],
            "links": [{"id": "l1", "from": ["r", "output"], "to": ["t", "input"]}],
        }
    )
    cache = tmp / "cache"
    res = run_flow(flow, Registry.discover(), cache / RUN)
    assert res.status == "done", res.error
    settings = Settings(meta_dir=tmp / "meta", cache_dir=cache, plugins_dir=tmp / "plugins")
    prebuild, ds.PREBUILD_MAX_ZOOM = ds.PREBUILD_MAX_ZOOM, -1  # pas de tuiles d'avance (sauf test dédié)
    with TestClient(create_app(settings)) as client:
        client.cache = cache
        ready(client)
        yield client
    ds.PREBUILD_MAX_ZOOM = prebuild


def url(path: str, node="r", port="output") -> str:
    return f"/api/data/{RUN}/{node}/{port}/{path}"


def ready(env, node="r", port="output") -> dict:
    """Description du port une fois sa carte préparée (en arrière-plan) ou abandonnée."""
    deadline = time.monotonic() + 60
    while True:
        info = env.get(url("info", node, port)).json()
        if (info["geometry"] or {}).get("map_status") != "building" or time.monotonic() > deadline:
            return info
        time.sleep(0.05)


def test_info(env):
    info = env.get(url("info")).json()
    assert info["total"] == N and [c["name"] for c in info["columns"]] == ["id", "nom", "pop", "dep"]
    assert info["columns"][2]["category"] == "numeric" and info["columns"][3]["category"] == "text"
    geometry = info["geometry"]
    assert geometry["types"] == ["POINT"] and geometry["crs"] == "EPSG:2154" and geometry["map"]
    assert geometry["map_status"] == "ready" and geometry["map_error"] is None
    assert len(geometry["extent_3857"]) == 4


def test_rows_paging_and_sorting(env):
    body = {"limit": 5, "offset": 10, "sort": [{"column": "pop", "desc": True}]}
    data = env.post(url("rows"), json=body).json()
    assert data["columns"] == ["fid", "id", "nom", "pop", "dep", "geometry"]
    assert data["total"] == N and data["filtered"] == N
    assert [r[3] for r in data["rows"]] == [3900, 3800, 3700, 3600, 3500]
    assert data["rows"][0][5] == "POINT"  # type de géométrie, pas la géométrie
    # tri par défaut : ordre d'origine (fid)
    first = env.post(url("rows"), json={"limit": 3}).json()["rows"]
    assert [r[0] for r in first] == [0, 1, 2]


@pytest.mark.parametrize(
    ("flt", "expected"),
    [
        ({"column": "nom", "op": "contains", "value": "commune 1"}, 10),  # commune 1, 11…19
        ({"column": "nom", "op": "starts", "value": "commune 4"}, 10),  # commune 4, 41…49 (la 40 est nulle)
        ({"column": "dep", "op": "equals", "value": "09"}, 25),
        ({"column": "dep", "op": "not_equals", "value": "09"}, 25),
        ({"column": "pop", "op": "gte", "value": 4000}, 10),
        ({"column": "pop", "op": "between", "value": 1000, "value2": 1900}, 10),
        ({"column": "pop", "op": "in", "values": [0, 100, 9999]}, 2),
        ({"column": "nom", "op": "null"}, 5),
        ({"column": "nom", "op": "not_null"}, 45),
        ({"column": "pop", "op": "equals", "value": "300"}, 1),
        ({"column": "pop", "op": "gt", "value": "pas un nombre"}, 0),
    ],
)
def test_column_filters(env, flt, expected):
    data = env.post(url("rows"), json={"filters": [flt], "limit": 100}).json()
    assert data["filtered"] == expected and len(data["rows"]) == expected and data["total"] == N


def test_search_where_and_combination(env):
    assert env.post(url("rows"), json={"search": "COMMUNE 7"}).json()["filtered"] == 1  # insensible à la casse
    assert env.post(url("rows"), json={"where": '"pop" > 4000 AND "dep" = \'31\''}).json()["filtered"] == 4
    # la clause libre peut utiliser la géométrie
    far = env.post(url("rows"), json={"where": "ST_X(geometry) > 609000"}).json()["filtered"]
    assert 0 < far < N
    both = env.post(
        url("rows"), json={"filters": [{"column": "dep", "op": "equals", "value": "31"}], "where": '"pop" < 1000'}
    )
    assert both.json()["filtered"] == 5


def test_stats(env):
    s = env.get(url("stats?geometry=true")).json()
    assert s["columns"]["pop"] == {"type": "BIGINT", "min": 0, "max": 4900, "distinct": 50, "nulls": 0}
    assert s["columns"]["nom"]["nulls"] == 5 and s["columns"]["dep"]["distinct"] == 2
    assert s["geometry"]["types"] == ["POINT"] and s["geometry"]["invalid"] == 0 and s["geometry"]["null"] == 0


def test_values_locate_and_feature(env):
    v = env.post(url("values"), json={"column": "dep"}).json()
    assert {x["value"]: x["count"] for x in v} == {"31": 25, "09": 25}
    q = {"sort": [{"column": "pop", "desc": True}]}
    assert env.post(url("locate"), json={"query": q, "fid": 49}).json() == {"index": 0}
    assert env.post(url("locate"), json={"query": q, "fid": 0}).json() == {"index": 49}
    filtered = {"filters": [{"column": "pop", "op": "lt", "value": 100}]}
    assert env.post(url("locate"), json={"query": filtered, "fid": 20}).json() == {"index": None}
    f = env.get(url("feature/7")).json()
    assert f["attributes"]["id"] == 7 and f["attributes"]["pop"] == 700
    west, south, east, north = f["bbox"]
    assert -2 < west <= east < 6 and 40 < south <= north < 50  # WGS84, quelque part en France
    assert env.get(url("feature/9999")).status_code == 404


def test_export_filtered_view(env, tmp_path):
    body = {"filters": [{"column": "dep", "op": "equals", "value": "31"}], "sort": [{"column": "pop", "desc": True}]}
    csv = env.post(url("export?format=csv"), json=body)
    lines = csv.content.decode().strip().splitlines()
    assert len(lines) == 26 and lines[0].startswith('"id"') and "wkt" in lines[0]
    pq_resp = env.post(url("export?format=parquet"), json=body)
    (tmp_path / "e.parquet").write_bytes(pq_resp.content)
    table = pq.read_table(tmp_path / "e.parquet")
    assert table.num_rows == 25 and table.column("pop")[0].as_py() == 4800
    gpkg = env.post(url("export?format=gpkg"), json=body)
    (tmp_path / "e.gpkg").write_bytes(gpkg.content)
    import pyogrio

    assert len(pyogrio.read_info(tmp_path / "e.gpkg", layer="output")["fields"]) == 4
    assert env.post(url("export?format=csv"), json={"where": '"pop" < 0'}).status_code == 422
    assert not list((env.cache / "_exports").glob("*/*"))  # fichiers temporaires nettoyés


def b64(q: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(q).encode()).decode().rstrip("=")


def tile_url(z=8, x=129, y=91, node="r", port="output", q=None, color=None) -> str:
    extra = (f"?q={b64(q)}" if q else "?") + (f"&color={color}" if color else "")
    return f"/tiles/{RUN}/{node}/{port}/{z}/{x}/{y}.pbf{extra}"


def decode(resp) -> list[dict]:
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/x-protobuf"
    return mvt.decode(resp.content).get("data", {}).get("features", [])


def test_tiles_contain_features_with_fid_and_color(env):
    feats = decode(env.get(tile_url(color="pop")))
    assert len(feats) == N
    props = {f["id"]: f["properties"]["c"] for f in feats}
    assert props[7] == 700 and props[49] == 4900
    assert {f["geometry"]["type"] for f in feats} == {"Point"}
    assert decode(env.get(tile_url(z=8, x=129, y=89))) == []  # tuile voisine vide
    assert len(decode(env.get(tile_url(z=3, x=4, y=2)))) == N  # petite échelle : tout est là


def test_tile_filter_is_applied_and_part_of_the_cache_key(env):
    q = {"filters": [{"column": "pop", "op": "lt", "value": 1000}]}
    assert len(decode(env.get(tile_url(q=q)))) == 10
    assert len(decode(env.get(tile_url()))) == N  # sans filtre : autre clé, autre contenu
    where = {"where": "ST_X(geometry) > 609000"}
    assert 0 < len(decode(env.get(tile_url(q=where)))) < N


def test_tiles_are_cached_on_disk_and_served_with_cache_headers(env):
    import gzip

    first = env.get(tile_url(z=7, x=64, y=45))
    assert first.headers["content-encoding"] == "gzip"  # gardée et envoyée compressée (le client la décompresse)
    cached = list((env.cache / RUN / "_tiles").rglob("45.pbf"))
    assert len(cached) == 1 and gzip.decompress(cached[0].read_bytes()) == first.content
    again = env.get(tile_url(z=7, x=64, y=45))
    assert again.content == first.content and "immutable" in again.headers["cache-control"]


def test_large_tiles_are_sampled(env, monkeypatch):
    monkeypatch.setattr(ds, "TILE_CAP", 10)
    n = len(decode(env.get(tile_url(color=None, q={"search": "commune"}))))
    assert 0 < n < N


def test_tile_errors(env):
    assert env.get(tile_url(z=30)).status_code == 400
    assert env.get(tile_url(z=2, x=9, y=0)).status_code == 400
    assert env.get(tile_url(color="inconnue")).status_code == 422
    assert env.get("/tiles/" + RUN + "/r/output/8/129/91.pbf?q=%%%").status_code == 400
    assert env.get(tile_url(node="nope")).status_code == 404


def test_feature_has_its_geometry_for_the_selection(env):
    f = env.get(url("feature/42")).json()
    assert f["geometry"]["type"] == "Point" and f["bbox"][0] == pytest.approx(f["geometry"]["coordinates"][0])


def write_points(node, n=5):
    folder = node.parent / node.name
    folder.mkdir()
    xs = np.linspace(600_000, 610_000, n)
    t = pa.table({"id": list(range(n)), GEOM: pa.array(shapely.to_wkb(shapely.points(xs, xs * 0 + 6.5e6)))})
    pq.write_table(with_crs(t, "EPSG:2154"), folder / "output.parquet")


def test_the_map_is_prepared_in_the_background_while_the_table_is_ready(env, monkeypatch):
    write_points(env.cache / RUN / "slow")
    go = threading.Event()
    real = ds.views.ensure_view

    def slow(path):
        go.wait(30)
        return real(path)

    monkeypatch.setattr(ds.views, "ensure_view", slow)
    geometry = env.get(url("info", node="slow")).json()["geometry"]
    assert geometry["map_status"] == "building" and not geometry["map"]  # réponse immédiate, sans attendre la carte
    assert env.post(url("rows", node="slow"), json={}).json()["filtered"] == 5  # la table, elle, est là
    assert env.get(tile_url(z=3, x=4, y=2, node="slow")).status_code == 409
    go.set()
    geometry = ready(env, node="slow")["geometry"]
    assert geometry["map_status"] == "ready" and geometry["map"] and len(geometry["extent_3857"]) == 4
    assert len(decode(env.get(tile_url(z=3, x=4, y=2, node="slow")))) == 5


def test_a_map_too_big_for_memory_says_so(env, monkeypatch):
    import duckdb

    write_points(env.cache / RUN / "huge")

    def out_of_memory(path):
        raise duckdb.OutOfMemoryException("Out of Memory Error: could not allocate block")

    monkeypatch.setattr(ds.views, "ensure_view", out_of_memory)
    geometry = ready(env, node="huge")["geometry"]
    assert (geometry["map_status"], geometry["map_error"], geometry["map"]) == ("failed", "memory", False)
    assert env.post(url("rows", node="huge"), json={}).status_code == 200  # la table reste consultable


def test_no_map_without_geometry(env, tmp_path):
    # un port sans géométrie : table oui (N premières lignes comprises), tuiles non
    plain = env.cache / RUN / "plain"
    plain.mkdir()
    pq.write_table(pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]}), plain / "output.parquet")
    info = env.get(url("info", node="plain")).json()
    assert info["total"] == 3 and info["geometry"] is None
    assert env.post(url("rows", node="plain"), json={"sort": [{"column": "b", "desc": True}]}).json()["rows"][0][
        1:
    ] == [3, "z"]
    assert env.get(tile_url(node="plain")).status_code == 404


def test_slow_queries_are_interrupted(env, monkeypatch):
    monkeypatch.setattr(ds, "QUERY_TIMEOUT", 0.5)
    r = env.post(url("rows"), json={"where": "pg_sleep(10) = 1"})
    assert r.status_code == 504 and "trop longue" in r.json()["detail"]
    assert env.post(url("rows"), json={}).status_code == 200  # la connexion reste utilisable


def test_security_identifiers_and_free_sql(env, tmp_path):
    assert env.get("/api/data/..%2F..%2Fetc/x/y/info").status_code in (400, 404)
    assert env.get(f"/api/data/{RUN}/r/..%2F..%2Fsecret/info").status_code in (400, 404)
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    for evil in (
        f"(SELECT count(*) FROM read_csv('{secret.as_posix()}')) > 0",
        "1=1; DROP TABLE x",
        "1) OR (1=1",
        "(SELECT count(*) FROM read_text('/etc/passwd')) > 0",
    ):
        r = env.post(url("rows"), json={"where": evil})
        assert r.status_code == 422, (evil, r.text)
    # un nom de colonne inventé n'est jamais interpolé tel quel
    bad = env.post(url("rows"), json={"filters": [{"column": 'pop" OR 1=1 --', "op": "equals", "value": 1}]})
    assert bad.status_code == 422
    assert env.post(url("rows"), json={"sort": [{"column": "geometry"}]}).status_code == 422


@pytest.mark.skipif(sys.platform == "win32", reason="os.replace se comporte autrement sous Windows ; testé sous Linux")
def test_two_requests_writing_the_same_tile_at_once_both_succeed(tmp_path, monkeypatch):
    """Régression : un nom de fichier temporaire commun faisait échouer la seconde requête (500) quand le navigateur
    demandait deux fois la même tuile (rechargement de la carte au redimensionnement du panneau)."""
    import threading
    from pathlib import Path

    from etl_api.data_service import write_atomically

    target = tmp_path / "tuiles" / "9" / "258" / "183.pbf"
    both_written = threading.Barrier(2, timeout=10)
    original = Path.write_bytes

    def write_then_wait(self, data):
        written = original(self, data)
        both_written.wait()  # les deux ont écrit avant que l'une renomme : le pire entrelacement
        return written

    monkeypatch.setattr(Path, "write_bytes", write_then_wait)
    errors = []

    def worker():
        try:
            write_atomically(target, b"tuile")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    monkeypatch.undo()
    assert errors == []
    assert target.read_bytes() == b"tuile"
    assert [p.name for p in target.parent.iterdir()] == ["183.pbf"]  # aucun .tmp ne traîne


def test_geometry_kind_is_read_from_the_wkb_header():
    import duckdb

    geoms = [
        shapely.Point(1, 2), shapely.LineString([(0, 0), (1, 1)]), shapely.box(0, 0, 1, 1),
        shapely.MultiPoint([(0, 0), (1, 1)]), shapely.MultiLineString([[(0, 0), (1, 1)]]),
        shapely.MultiPolygon([shapely.box(0, 0, 1, 1)]), shapely.GeometryCollection([shapely.Point(0, 0)]),
    ]  # fmt: skip
    expected = [g.geom_type.upper() for g in geoms] + [None]
    blobs = [shapely.to_wkb(g) for g in geoms] + [None]
    iso = [shapely.to_wkb(g, flavor="iso", output_dimension=3) for g in [shapely.Point(1, 2, 3)]]
    con = duckdb.connect()
    con.execute("CREATE TABLE t(i INTEGER, geometry BLOB)")
    for i, b in enumerate([*blobs, *iso]):
        con.execute("INSERT INTO t VALUES (?, ?)", [i, b])
    found = [r[0] for r in con.execute(f"SELECT {ds._wkb_kind('geometry')} FROM t ORDER BY i").fetchall()]
    assert found == [*expected, "POINT"]


def test_the_table_survives_geometries_too_big_to_read(env, monkeypatch):
    real = ds.Duck.run

    def run(self, sql, params=None, timeout=None):
        if "CASE" in sql:  # la lecture des géométries dépasse la mémoire de l'inspecteur
            raise ds.DataError("mémoire", ds.OUT_OF_MEMORY)
        return real(self, sql, params, timeout)

    monkeypatch.setattr(ds.Duck, "run", run)
    # pas de vue : le type serait lu dans les géométries
    monkeypatch.setattr(ds.views, "read_view_meta", lambda *a: None)
    monkeypatch.setattr(ds.views, "ensure_view", lambda *a: None)
    data = env.post(url("rows"), json={"limit": 3}).json()
    assert [r[1] for r in data["rows"]] == [0, 1, 2] and data["rows"][0][5] is None  # attributs là, type absent


def test_big_geometries_are_written_in_bounded_row_groups(monkeypatch):
    from etl_engine import cache

    monkeypatch.setattr(cache, "ROW_GROUP_BYTES", 1000)
    sizes = [10, 3000, 10, 10, 2500, 10]
    t = pa.table({"a": list(range(6)), GEOM: pa.array([b"x" * n for n in sizes], pa.binary())})
    parts = [p.column("a").to_pylist() for p in cache.row_groups(t)]
    assert sum(parts, []) == list(range(6)) and len(parts) > 1
    assert all(len(p) == 1 or sum(sizes[i] for i in p) <= 3100 for p in parts)
    small = pa.table({"a": [1, 2], GEOM: pa.array([b"x", b"y"], pa.binary())})
    assert len(list(cache.row_groups(small))) == 1


def test_huge_entity_tiles_and_selection_stay_light(env, monkeypatch):
    """Parcelles fusionnées en une seule entité : tuiles et sélection ne lisent que ce qu'il faut dessiner."""
    big = env.cache / RUN / "big"
    big.mkdir()
    rng = np.random.default_rng(3)
    xs, ys = rng.uniform(560_000, 640_000, 3000), rng.uniform(6_250_000, 6_330_000, 3000)
    multi = shapely.multipolygons(list(shapely.box(xs, ys, xs + 150, ys + 100)))
    t = pa.table({"nom": ["fusion"], GEOM: pa.array([shapely.to_wkb(multi)], pa.binary())})
    pq.write_table(with_crs(t, "EPSG:2154"), big / "output.parquet")

    info = ready(env, node="big")
    assert info["geometry"]["map"] and info["geometry"]["types"] == ["MULTIPOLYGON"]
    assert env.post(url("rows", node="big"), json={}).json()["rows"][0][-1] == "MULTIPOLYGON"  # type noté dans la vue

    x0, y0, x1, y1 = info["geometry"]["extent_3857"]
    world = 20037508.342789244

    def tile_at(z):
        tx = int(((x0 + x1) / 2 + world) / (2 * world) * 2**z)
        ty = int((world - (y0 + y1) / 2) / (2 * world) * 2**z)
        return decode(env.get(tile_url(z=z, x=tx, y=ty, node="big")))

    # petite échelle (pixel de 2,4 km, 80 km de côté : 113 km en Web Mercator) : un carré d'un pixel (8 unités) par
    # pixel occupé, pas 3 000 polygones
    small = tile_at(5)
    assert 0 < len(small) <= 47 * 47
    assert {len({x for x, _ in f["geometry"]["coordinates"][0]}) for f in small} == {2}
    assert {abs(f["geometry"]["coordinates"][0][0][0] - f["geometry"]["coordinates"][0][2][0]) for f in small} == {8}
    assert len(tile_at(11)) > 10  # grande échelle : les parcelles elles-mêmes
    assert {f["id"] for f in tile_at(12)} == {0}  # chaque morceau garde le fid de l'entité

    full = env.get(url("feature/0", node="big")).json()
    assert len(full["geometry"]["coordinates"]) == 3000  # entité ordinaire : géométrie exacte
    monkeypatch.setattr(ds, "FEATURE_MAX_POINTS", 100)
    monkeypatch.setattr(ds, "FEATURE_PIXELS", 100)
    light = env.get(url("feature/0", node="big")).json()
    assert light["geometry"]["type"] == "MultiPolygon"
    # de petites parcelles éparses : une cellule par pixel occupé (fusionnées si voisines), pas leurs 15 000 sommets
    assert sum(len(ring) for poly in light["geometry"]["coordinates"] for ring in poly) < 3000 * 5
    assert light["bbox"] == pytest.approx(full["bbox"], abs=1e-6)


def test_too_many_points_are_sampled_the_same_way_in_every_tile(env, monkeypatch):
    monkeypatch.setattr(ds, "MAP_LIMIT", 10)  # fait partie de la clé des tuiles : pas de tuile d'avant
    sampled = {f["id"] for f in decode(env.get(tile_url(z=3, x=4, y=2)))}
    assert 0 < len(sampled) <= 10  # un sur 6 (paliers 1, 2, 3, 4, 6…) : au plus 10
    assert sampled == {f["id"] for f in decode(env.get(tile_url(z=8)))}  # même échantillon à chaque zoom


def test_a_crowded_area_draws_its_largest_entities_whatever_the_view(env, monkeypatch):
    """Au-delà de la limite, les plus grandes entités de la métatuile (pas un échantillon au hasard, pas les premières
    du port) : les mêmes dans toutes les tuiles, quelle que soit la vue (la tuile ne dépend que de sa position) ; les
    points attendent qu'on zoome."""
    folder = env.cache / RUN / "sizes"
    folder.mkdir()
    sides = [100 * (i + 1) for i in range(40)]  # 40 carrés de 100 m à 4 km, en ligne
    xs = np.cumsum([0, *[s + 500 for s in sides[:-1]]]) + 560_000
    squares = shapely.box(xs, 6_280_000, xs + sides, np.add(6_280_000, sides))
    geoms = [*squares, *shapely.points(xs, np.full(40, 6_270_000))]  # et 40 points
    t = pa.table({"side": [*sides, *[0] * 40], GEOM: pa.array(shapely.to_wkb(geoms), pa.binary())})
    pq.write_table(with_crs(t, "EPSG:2154"), folder / "output.parquet")
    x0, y0, x1, y1 = ready(env, node="sizes")["geometry"]["extent_3857"]
    world = 20037508.342789244

    def tile_at(z):
        tx = int(((x0 + x1) / 2 + world) / (2 * world) * 2**z)
        ty = int((world - (y0 + y1) / 2) / (2 * world) * 2**z)
        return {f["id"] for f in decode(env.get(tile_url(z=z, x=tx, y=ty, node="sizes")))}

    every = tile_at(6)  # sans limite : tout (deux petits carrés dans le même pixel n'en font qu'un)
    assert set(range(40, 80)) <= every and len(every) >= 75
    monkeypatch.setattr(ds, "MAP_LIMIT", 10)
    drawn = tile_at(6)
    assert 0 < len(drawn) <= 10 and drawn == set(range(40 - len(drawn), 40))  # les plus grands carrés, aucun point
    assert drawn == tile_at(5)  # les mêmes à chaque zoom


def test_small_scale_tiles_are_prepared_in_advance(env, monkeypatch):
    write_points(env.cache / RUN / "ahead")
    monkeypatch.setattr(ds, "PREBUILD_MAX_ZOOM", 4)
    info = ready(env, node="ahead")
    assert ds.prebuild_tiles(info["geometry"]["extent_3857"])[:2] == [(0, 0, 0), (1, 1, 0)]
    folder = env.cache / RUN / "_tiles" / "ahead__output"
    deadline = time.monotonic() + 30
    while len(list(folder.rglob("*.pbf"))) < 5 and time.monotonic() < deadline:  # zooms 0 à 4 : une tuile chacun
        time.sleep(0.05)
    assert sorted(int(f.parent.parent.name) for f in folder.rglob("*.pbf")) == [0, 1, 2, 3, 4]
    world = 20037508.342789244
    monkeypatch.setattr(ds, "PREBUILD_MAX_ZOOM", 10)
    assert ds.prebuild_tiles([-world, -world, world, world]) == [  # le monde : arrêt avant 2 000 tuiles (zoom 6)
        (z, x, y) for z in range(6) for x in range(2**z) for y in range(2**z)
    ]


def test_the_map_tiles_go_first_and_abandoned_ones_leave_the_queue():
    """Une tuile que la carte ne demande plus (autre port affiché) quitte la file sans calcul ; les tuiles calculées
    d'avance passent après celles que la carte attend."""
    gate = ds._Gate(1)
    order: list[str] = []
    busy, release, gone = threading.Event(), threading.Event(), threading.Event()

    def hold():
        with gate.slot(lambda: False):
            busy.set()
            release.wait(10)

    def take(name, ahead=False, abandoned=lambda: False):
        try:
            with gate.slot(abandoned, ahead):
                order.append(name)
        except ds.Abandoned:
            order.append(f"{name} abandonnée")

    threads = [threading.Thread(target=hold)]
    threads[0].start()
    assert busy.wait(10)
    for args in (("d'avance", True), ("port précédent", False, gone.is_set), ("port affiché",)):
        threads.append(threading.Thread(target=take, args=args))
        threads[-1].start()
        time.sleep(0.05)
    gone.set()
    deadline = time.monotonic() + 10
    while not order and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    [t.join(10) for t in threads]
    assert order == ["port précédent abandonnée", "port affiché", "d'avance"]


def test_a_tile_the_browser_gave_up_on_is_dropped(env, monkeypatch):
    """La carte annule la requête d'une tuile (autre port affiché) : sur un vrai serveur, la tuile en attente d'une
    place quitte la file."""
    import socket

    import uvicorn

    monkeypatch.setattr(ds, "_GATE", ds._Gate(0))  # aucune place : la tuile attend son tour
    outcome: list[str] = []
    real = ds.tile

    def tile(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except ds.Abandoned:
            outcome.append("abandonnée")
            raise

    monkeypatch.setattr(ds, "tile", tile)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(env.app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        client = socket.create_connection(sock.getsockname())
        client.sendall(f"GET {tile_url(q={'search': 'abandon'})} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        time.sleep(0.3)
        assert outcome == []  # elle attend
        client.close()  # le navigateur l'annule
        while not outcome and time.monotonic() < deadline:
            time.sleep(0.01)
        assert outcome == ["abandonnée"]
    finally:
        server.should_exit = True
        thread.join(10)


def test_only_the_last_opened_port_has_its_tiles_prepared_in_advance(env, monkeypatch):
    write_points(env.cache / RUN / "first")
    ready(env, node="first")
    port = ds.open_port(env.app.state.duck, RUN, "first", "output", prepare=False)
    port.map_status, port.meta, _ = ds._map_view(port.raw)
    port.view, port.entities = ds.views.view_path(port.raw), ds.views.entities_path(port.raw)
    key = (port.view, port.view.stat().st_mtime_ns)
    monkeypatch.setattr(ds, "PREBUILD_MAX_ZOOM", 4)
    monkeypatch.setattr(ds, "_prebuilt", set())
    folder = env.cache / RUN / "_tiles" / "first__output"

    monkeypatch.setattr(ds, "_ahead_of", ("un autre port", 0))
    ds._prebuild_tiles(port, key)
    assert not list(folder.rglob("*.pbf")) and key not in ds._prebuilt  # arrêté : il reprendra à sa prochaine ouverture
    monkeypatch.setattr(ds, "_ahead_of", key)
    ds._prebuild_tiles(port, key)
    assert len(list(folder.rglob("*.pbf"))) == 5 and key in ds._prebuilt


def test_the_map_of_the_port_just_opened_is_prepared_first(env, monkeypatch):
    """Quatre ports ouverts l'un après l'autre : le dernier passe devant ceux qui attendent encore leur tour."""
    names = ["v1", "v2", "v3", "v4"]
    for name in names:
        write_points(env.cache / RUN / name)
    go = threading.Event()
    started: list[str] = []
    real = ds.views.ensure_view

    def slow(path):
        started.append(path.parent.name)
        go.wait(30)
        return real(path)

    monkeypatch.setattr(ds.views, "ensure_view", slow)
    for name in names[:3]:
        assert env.get(url("info", node=name)).json()["geometry"]["map_status"] == "building"
    deadline = time.monotonic() + 10
    while len(started) < 2 and time.monotonic() < deadline:  # deux vues à la fois : v3 attend son tour
        time.sleep(0.01)
    env.get(url("info", node="v4"))
    go.set()
    assert ready(env, node="v4")["geometry"]["map_status"] == "ready"
    assert sorted(started[:2]) == ["v1", "v2"] and started[2] == "v4"
    assert ready(env, node="v3")["geometry"]["map_status"] == "ready"  # relancée à sa demande suivante


def test_a_port_that_kept_geoparquet_metadata_is_inspected(env):
    """Régression : un port lu d'un GeoParquet garde ses métadonnées « geo » ; DuckDB lisait alors la géométrie en
    GEOMETRY (« ARRAY_SLICE can only operate on LISTs and VARCHARs », carte absente)."""
    node = env.cache / RUN / "geo"
    node.mkdir()
    xs = np.linspace(600_000, 610_000, 5)
    t = with_crs(
        pa.table({"id": list(range(5)), GEOM: pa.array(shapely.to_wkb(shapely.points(xs, xs * 0 + 6.5e6)))}),
        "EPSG:2154",
    )
    geo = {"version": "1.1.0", "primary_column": GEOM, "columns": {GEOM: {"encoding": "WKB", "geometry_types": []}}}
    pq.write_table(
        t.replace_schema_metadata({**t.schema.metadata, b"geo": json.dumps(geo).encode()}), node / "output.parquet"
    )
    assert ready(env, node="geo")["geometry"]["map"]
    assert [r[-1] for r in env.post(url("rows", node="geo"), json={}).json()["rows"]] == ["POINT"] * 5
    assert env.get(url("stats", node="geo")).status_code == 200
    assert len(decode(env.get(tile_url(z=3, x=4, y=2, node="geo")))) == 5
