import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.flow import Flow
from etl_engine.registry import Registry


@pytest.fixture(scope="session")
def registry() -> Registry:
    return Registry.discover()


@pytest.fixture
def communes(tmp_path):
    """GeoParquet de 20 points « communes » : id, nom, pop, dep."""
    n = 20
    xs = np.linspace(600000, 610000, n)
    t = pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "nom": pa.array([f"commune {i}" for i in range(n)]),
            "pop": pa.array([i * 100 for i in range(n)], pa.int64()),
            "dep": pa.array(["31" if i % 2 == 0 else "09" for i in range(n)]),
            GEOM: pa.array(shapely.to_wkb(shapely.points(xs, np.full(n, 6.5e6))), pa.binary()),
        }
    )
    path = tmp_path / "communes.parquet"
    pq.write_table(with_crs(t, "EPSG:2154"), path)
    return path


def make_flow(nodes: list[dict], links: list[tuple[str, str, str, str]], **extra) -> Flow:
    return Flow.model_validate(
        {
            "name": "test",
            "nodes": nodes,
            "links": [{"id": f"l{i}", "from": [a, ap], "to": [b, bp]} for i, (a, ap, b, bp) in enumerate(links)],
            **extra,
        }
    )
