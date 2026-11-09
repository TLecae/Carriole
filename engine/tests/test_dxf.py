"""DXF (AutoCAD) : écriture d'un dessin (calques, textes, polygones en contours ou en hachures), relecture."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine import formats
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow
from etl_engine.sdk import ConfigError

from conftest import make_flow


def drawing():
    geoms = [
        shapely.Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (4, 2), (4, 4), (2, 4)]]),
        shapely.LineString([(0, 0), (20, 5)]),
        shapely.Point(5, 5),
        shapely.Point(6, 6),
        None,
    ]
    return with_crs(
        pa.table(
            {
                "nature": ["Bâti", "Voie", "Lieu-dit", "Repère", "Vide"],
                "nom": ["Mairie", None, 'Le "Pré" \\ nord', "", None],
                "surface": [100.0, None, None, None, None],
                GEOM: pa.array(shapely.to_wkb(geoms), pa.binary()),
            }
        ),
        "EPSG:2154",
    )


def write(path, table, **opts):
    writer = formats.open_writer("dxf", str(path), **opts)
    writer.write(table)
    writer.close()
    return writer


def read(path, **opts):
    return pa.concat_tables(formats.read_batches("dxf", str(path), 1000, **opts))


def test_layers_texts_and_polygon_outlines_are_written_and_read_back(tmp_path):
    target = tmp_path / "plan.dxf"
    writer = write(target, drawing(), layer_attribute="nature", text_attribute="nom")
    assert writer.messages == [
        "DXF : 1 entité(s) sans géométrie non écrite(s)",
        "DXF : attributs non écrits (seuls le calque et le texte le sont) : surface",
    ]
    lines = read(target, closed_as_polygons=False)
    assert lines.column("Layer").to_pylist() == ["Bâti", "Bâti", "Voie", "Lieu-dit", "Repère"]  # contour : 2 anneaux
    assert lines.column("Text").to_pylist()[3:] == ['Le "Pré" \\ nord', None]  # texte des points seulement
    types = [shapely.from_wkb(g).geom_type for g in lines.column(GEOM).to_pylist()]
    assert types == ["LineString", "LineString", "LineString", "Point", "Point"]
    assert get_crs(lines) is None  # le DXF ne porte pas de système de coordonnées

    polygons = read(target, crs="EPSG:2154", layers=["Bâti", "Voie"])
    assert get_crs(polygons) == "EPSG:2154"
    assert [shapely.from_wkb(g).geom_type for g in polygons.column(GEOM).to_pylist()] == [
        "Polygon",
        "Polygon",
        "LineString",
    ]
    assert formats.read_schema("dxf", str(target), crs="EPSG:2154").names[0] == "Layer"


def test_polygons_as_hatches_keep_their_holes(tmp_path):
    target = tmp_path / "plan.dxf"
    write(target, drawing().slice(0, 1), polygons="hachure")
    found = read(target)
    polygon = shapely.from_wkb(found.column(GEOM)[0].as_py())
    assert found.num_rows == 1 and polygon.geom_type == "Polygon" and len(polygon.interiors) == 1
    assert found.column("Layer").to_pylist() == ["0"]  # sans attribut « Layer » : le calque 0


def test_a_dxf_is_not_appended_and_needs_geometries(tmp_path):
    with pytest.raises(ConfigError, match="ne se complète pas"):
        formats.open_writer("dxf", str(tmp_path / "plan.dxf"), mode="append")
    with pytest.raises(ConfigError, match="n'en ont pas"):
        write(tmp_path / "plan.dxf", pa.table({"a": [1]}))
    with pytest.raises(ConfigError, match="attribut du calque absent des données : calque"):
        write(tmp_path / "plan.dxf", drawing(), layer_attribute="calque")
    assert not list(tmp_path.iterdir())  # rien d'écrit


def test_a_flow_reads_a_drawing_and_writes_it_back(registry, tmp_path):
    source = tmp_path / "communes.parquet"
    pq.write_table(drawing(), source)
    nodes = [
        {"id": "read", "type": "GeoParquetReader", "params": {"path": str(source)}},
        {
            "id": "dxf",
            "type": "DxfWriter",
            "params": {"path": str(tmp_path / "plan.dxf"), "layer_attribute": "nature", "polygons": "hachure"},
        },
    ]
    res = run_flow(make_flow(nodes, [("read", "output", "dxf", "input")]), registry, tmp_path / "run", cache=False)
    assert res.status == "done", res.error
    nodes = [
        {
            "id": "back",
            "type": "DxfReader",
            "params": {"path": str(tmp_path / "plan.dxf"), "crs": "EPSG:2154", "where": "\"Layer\" <> 'Voie'"},
        }
    ]
    res = run_flow(make_flow(nodes, []), registry, tmp_path / "run2", cache=False)
    assert res.status == "done", res.error
    assert res.nodes["back"].rows == {"output": 3}
