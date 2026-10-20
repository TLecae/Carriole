from pathlib import Path

import numpy as np
import pyarrow as pa
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.registry import Registry
from etl_engine.testing import run_modifier

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "modifiers"


def test_surface_calculator_example():
    reg = Registry()
    reg.load_directory(EXAMPLES)
    cls = reg.get("SurfaceCalculator")
    geoms = np.array(
        [
            shapely.box(0, 0, 10, 10),
            shapely.Point(1, 1),
            shapely.box(0, 0, 100, 100),
            None,
            shapely.MultiPolygon([shapely.box(0, 0, 1, 1)]),
        ],
        dtype=object,
    )
    t = with_crs(pa.table({"id": [1, 2, 3, 4, 5], GEOM: pa.array(shapely.to_wkb(geoms), pa.binary())}), "EPSG:2154")
    out = run_modifier(cls, {"unit": "m2"}, t)
    assert out["output"].column("surface").to_pylist() == [100.0, 10000.0, 1.0]
    assert out["Rejected"].column("id").to_pylist() == [2, 4]
    assert set(out["Rejected"].column("_rejection_reason").to_pylist()) == {"pas de polygone"}
    assert run_modifier(cls, {"unit": "ha"}, t)["output"].column("surface").to_pylist() == [0.01, 1.0, 0.0]
