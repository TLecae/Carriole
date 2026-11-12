"""GeoParquet lu à une adresse web : par plages (pas de téléchargement complet), comme un fichier local."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import formats
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.modifiers.file_io import CsvReader, GeoParquetReader, GeoParquetWriter
from etl_engine.sdk import ConfigError, Context
from etl_engine.testing import run_transformer


class Site:
    """Un serveur web local qui sert un dossier, avec les requêtes HEAD et par plages (Range) des vrais serveurs."""

    def __init__(self, root: Path) -> None:
        self.root, self.sent = root, 0
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D102 - silencieux
                pass

            def _file(self) -> Path | None:
                target = site.root / self.path.lstrip("/").split("?")[0]
                return target if target.is_file() else None

            def _headers(self, target: Path, status: int, length: int, start: int = 0) -> None:
                st = target.stat()
                self.send_response(status)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", f'"{st.st_mtime_ns}-{st.st_size}"')
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{st.st_size}")
                self.end_headers()

            def do_HEAD(self):  # noqa: N802
                target = self._file()
                if target is None:
                    self.send_error(404)
                    return
                self._headers(target, 200, target.stat().st_size)

            def do_GET(self):  # noqa: N802
                target = self._file()
                if target is None:
                    self.send_error(404)
                    return
                data = target.read_bytes()
                wanted = self.headers.get("Range", "")
                if wanted.startswith("bytes="):
                    first, _, last = wanted[6:].partition("-")
                    start, end = int(first), min(int(last) if last else len(data) - 1, len(data) - 1)
                    self._headers(target, 206, end - start + 1, start)
                    chunk = data[start : end + 1]
                else:
                    self._headers(target, 200, len(data))
                    chunk = data
                site.sent += len(chunk)
                self.wfile.write(chunk)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def site(tmp_path):
    served = Site(tmp_path)
    yield served
    served.server.shutdown()


def parcels(n: int) -> pa.Table:
    rng = np.random.default_rng(1)
    xs, ys = rng.uniform(560_000, 640_000, n), rng.uniform(6_250_000, 6_330_000, n)
    t = pa.table({"id": np.arange(n), "nom": [f"parcelle {i}" for i in range(n)]})
    return with_crs(t.append_column(GEOM, pa.array(shapely.to_wkb(shapely.box(xs, ys, xs + 20, ys + 20)))), "EPSG:2154")


def read(params):
    return run_transformer(GeoParquetReader, params, {})["output"]


def test_a_web_geoparquet_reads_like_the_same_local_file(site, tmp_path):
    local = tmp_path / "parcelles.parquet"
    run_transformer(GeoParquetWriter, {"path": str(local)}, {"input": parcels(500)})
    web = read({"path": f"{site.url}/parcelles.parquet", "add_filename": True})
    same = read({"path": str(local)})
    assert web.num_rows == 500 and get_crs(web) == get_crs(same) == "EPSG:2154"
    assert web.column("id").to_pylist() == same.column("id").to_pylist()  # même ordre
    assert all(a.equals(b) for a, b in zip(geometries(web)[:20], geometries(same)[:20], strict=True))
    assert set(web.column("filename").to_pylist()) == {"parcelles.parquet"}
    schema = formats.read_schema("parquet", f"{site.url}/parcelles.parquet")
    assert schema.names == same.schema.names and get_crs(schema) == "EPSG:2154"


def native(path: Path, geoms, encoding: str, nulls: list[bool], interleaved: bool = False) -> None:
    """GeoParquet 1.1 en encodage GeoArrow natif (coordonnées en listes imbriquées), colonne « geometrie »."""
    _, coords, offsets = shapely.to_ragged_array(geoms)
    mask, dims = pa.array(nulls), coords.shape[1]
    point_mask = None if offsets else mask  # points : les entités nulles sont les coordonnées nulles
    if interleaved:
        values = pa.FixedSizeListArray.from_arrays(pa.array(coords.ravel()), dims, mask=point_mask)
    else:
        values = pa.StructArray.from_arrays(
            [pa.array(coords[:, i]) for i in range(dims)], "xyz"[:dims], mask=point_mask
        )
    for i, o in enumerate(offsets):  # des anneaux vers les entités
        values = pa.ListArray.from_arrays(pa.array(o, pa.int32()), values, mask=mask if i == len(offsets) - 1 else None)
    geo = {
        "version": "1.1.0",
        "primary_column": "geometrie",
        "columns": {"geometrie": {"encoding": encoding, "geometry_types": []}},
    }
    table = pa.table({"id": np.arange(len(geoms)), "geometrie": values})
    pq.write_table(table.replace_schema_metadata({"geo": json.dumps(geo)}), path, row_group_size=2)


def test_a_native_geoarrow_geoparquet_is_read_as_wkb_locally_and_on_the_web(site, tmp_path):
    polygons = shapely.multipolygons([[shapely.box(i, 0, i + 1, 1), shapely.box(i, 2, i + 1, 3)] for i in range(5)])
    with_hole = shapely.Polygon([(0, 0), (9, 0), (9, 9), (0, 9)], [[(1, 1), (2, 1), (2, 2), (1, 2)]])
    polygons[3] = shapely.MultiPolygon([with_hole])
    native(tmp_path / "zones.parquet", polygons, "multipolygon", [False, False, True, False, False])
    points = shapely.points([[0, 0, 1], [1, 1, 2], [2, 2, 3]])
    native(tmp_path / "points.parquet", points, "point", [False, True, False], interleaved=True)
    expected = list(polygons)
    expected[2] = None
    for path in (str(tmp_path / "zones.parquet"), f"{site.url}/zones.parquet"):
        out = read({"path": path})
        assert out.schema.field(GEOM).type == pa.binary() and get_crs(out) == "OGC:CRS84"
        got = geometries(out)
        assert [g is None for g in got] == [e is None for e in expected], path
        assert all(g is None or g.equals(e) for g, e in zip(got, expected, strict=True)), path
        assert formats.read_schema("parquet", path).field(GEOM).type == pa.binary()
        assert formats.sql_query("parquet", [path]) is None  # DuckDB lirait les listes : la lecture passe par lots
    out = read({"path": str(tmp_path / "points.parquet")})
    got = geometries(out)
    assert got[1] is None and got[2].equals(points[2]) and shapely.get_coordinates(got[2], include_z=True)[0, 2] == 3


def test_appending_to_a_native_geoparquet_rewrites_it_in_wkb(tmp_path):
    target = tmp_path / "zones.parquet"
    native(target, shapely.points([[0, 0], [1, 1]]), "point", [False, False])
    more = with_crs(
        pa.table({"id": [9]}).append_column(GEOM, pa.array(shapely.to_wkb(shapely.points([[5, 5]])))), "OGC:CRS84"
    )
    run_transformer(GeoParquetWriter, {"path": str(target), "mode": "append"}, {"input": more})
    out = read({"path": str(target)})
    assert out.column("id").to_pylist() == [0, 1, 9]
    assert [g.x for g in geometries(out)] == [0, 1, 5]


def test_only_the_needed_parts_of_a_web_file_are_downloaded(site, tmp_path):
    big = tmp_path / "gros.parquet"
    pq.write_table(parcels(200_000), big, row_group_size=10_000)
    size = big.stat().st_size
    assert read({"path": f"{site.url}/gros.parquet", "max_rows": 10}).num_rows == 10
    assert site.sent < size / 10, (site.sent, size)  # pied de page et premier groupe de lignes (5 %), pas tout


def test_a_changed_web_file_is_read_again_and_errors_are_clear(site, tmp_path):
    target = tmp_path / "zones.parquet"
    pq.write_table(parcels(5), target)
    reader = GeoParquetReader(GeoParquetReader.Params(path=f"{site.url}/zones.parquet"), Context())
    before = reader.fingerprint_extra()
    assert before == reader.fingerprint_extra()  # même fichier : le cache du run sert
    pq.write_table(parcels(7), target)
    os.utime(target, ns=(1, 1))
    assert reader.fingerprint_extra() != before  # ETag changé : relu

    with pytest.raises(ConfigError, match="lecture impossible"):
        read({"path": f"{site.url}/absent.parquet"})
    with pytest.raises(ConfigError, match="adresse web"):
        run_transformer(CsvReader, {"path": f"{site.url}/zones.csv"}, {})


def test_the_proxy_from_the_settings_is_used(monkeypatch):
    import duckdb
    from etl_engine import duck

    monkeypatch.setenv("HTTPS_PROXY", "http://agent:mot%40passe@proxy.exemple:3128")
    con = duckdb.connect()
    duck.load_httpfs(con)
    found = [
        con.execute(f"SELECT current_setting('{name}')").fetchone()[0] for name in ("http_proxy", "http_proxy_username")
    ]
    assert found == ["proxy.exemple:3128", "agent"]
