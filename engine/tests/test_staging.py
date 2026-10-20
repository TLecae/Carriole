"""Partage réseau : écriture en deux temps (voir etl_engine.staging)."""

import os

import pyogrio
import pytest
from etl_engine import paths, staging
from etl_engine.modifiers.reader import Reader
from etl_engine.modifiers.writer import Writer
from etl_engine.sdk import ConfigError, Context
from etl_engine.testing import points, run_modifier, table


@pytest.fixture
def share(tmp_path, monkeypatch):
    """Un « partage » : dossier autorisé en lecture et écriture, écrit en deux temps."""
    root = tmp_path / "partage"
    root.mkdir()
    scratch = tmp_path / "travail"
    monkeypatch.setenv("ETL_READ_ROOTS", str(root))
    monkeypatch.setenv("ETL_WRITE_ROOTS", str(root))
    monkeypatch.setenv("ETL_STAGED_ROOTS", str(root))
    monkeypatch.setenv("ETL_STAGING_DIR", str(scratch))
    return root


def scratch_of(share):
    return share.parent / "travail"


def leftovers(share):
    """Ce qu'une écriture ne doit jamais laisser : fichiers temporaires `.part` sur le partage, travail local."""
    parts = [p.name for p in share.rglob("*.part")]
    scratch = scratch_of(share)
    work = [p.name for p in scratch.iterdir()] if scratch.exists() else []
    return parts, work


def test_browse_roots_report_a_missing_folder(share, tmp_path, monkeypatch):
    (root,) = paths.browse_roots("read")
    assert root["available"] is True and root["reason"] is None
    monkeypatch.setenv("ETL_READ_ROOTS", str(tmp_path / "absent"))
    (root,) = paths.browse_roots("read")
    assert root["available"] is False and root["reason"]


# ------------------------------------------------------------------------ écriture en deux temps


def test_writer_publishes_in_one_piece_and_leaves_no_trace(share):
    target = share / "sortie.csv"
    run_modifier(Writer, {"path": str(target)}, table(a=[1, 2, 3, 4]))
    assert run_modifier(Reader, {"path": str(target)})["output"].column("a").to_pylist() == ["1", "2", "3", "4"]
    assert leftovers(share) == ([], [])


def test_failure_midway_leaves_the_destination_untouched(share):
    target = share / "sortie.csv"
    target.write_text("a\nancien\n", encoding="utf-8")

    def batches():
        yield table(a=["nouveau"])
        raise RuntimeError("panne en plein flux")

    writer = Writer(Writer.Params(path=str(target)), Context())
    with pytest.raises(RuntimeError, match="panne"):
        writer.run({"input": batches()}, lambda port, t: None)
    assert target.read_text(encoding="utf-8") == "a\nancien\n"  # ni vide, ni à moitié écrit
    assert leftovers(share) == ([], [])


def test_cancellation_leaves_the_destination_untouched(share):
    target = share / "sortie.csv"
    target.write_text("a\nancien\n", encoding="utf-8")
    ctx = Context()
    writer = Writer(Writer.Params(path=str(target)), ctx)

    def batches():
        yield table(a=[1])
        ctx.cancel.set()
        yield table(a=[2])

    from etl_engine.sdk import Cancelled

    with pytest.raises(Cancelled):
        writer.run({"input": batches()}, lambda port, t: None)
    assert target.read_text(encoding="utf-8") == "a\nancien\n"
    assert leftovers(share) == ([], [])


def test_failed_publication_keeps_the_old_file_and_removes_the_temporary(share, monkeypatch):
    target = share / "sortie.csv"
    target.write_text("a\nancien\n", encoding="utf-8")

    def broken(*_):
        raise OSError("liaison coupée")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="liaison coupée"):
        run_modifier(Writer, {"path": str(target)}, table(a=[1]))
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "a\nancien\n"
    assert not list(share.rglob("*.part"))


def test_geopackage_overwrite_replaces_only_its_layer(share):
    target = share / "donnees.gpkg"
    run_modifier(Writer, {"path": str(target), "layer": "a"}, table(x=[1, 2]))
    run_modifier(Writer, {"path": str(target), "layer": "b"}, table(y=[7]))
    run_modifier(Writer, {"path": str(target), "layer": "a"}, table(x=[9]))  # remplace « a », garde « b »
    layers = {name: pyogrio.read_info(target, layer=name)["features"] for name, _ in pyogrio.list_layers(target)}
    assert layers == {"a": 1, "b": 1}
    assert leftovers(share) == ([], [])


def test_append_starts_from_the_existing_file(share):
    target = share / "donnees.gpkg"
    run_modifier(Writer, {"path": str(target), "layer": "a"}, table(x=[1, 2]))
    run_modifier(Writer, {"path": str(target), "layer": "a", "mode": "append"}, table(x=[3]))
    assert pyogrio.read_info(target, layer="a")["features"] == 3


def test_create_mode_refuses_an_existing_file_without_touching_it(share):
    target = share / "sortie.csv"
    target.write_text("a\nancien\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="existe déjà"):
        run_modifier(Writer, {"path": str(target), "mode": "create"}, table(a=[1]))
    assert target.read_text(encoding="utf-8") == "a\nancien\n"
    assert leftovers(share) == ([], [])


def test_shapefile_publishes_its_files_and_spares_the_neighbours(share):
    (share / "donnees.csv").write_text("a\n1\n", encoding="utf-8")  # même nom, autre fichier : à ne pas toucher
    (share / "donnees.xyz").write_text("gardé", encoding="utf-8")
    target = share / "donnees.shp"
    run_modifier(Writer, {"path": str(target)}, points([1.0, 2.0], [1.0, 2.0]))
    names = {p.name for p in share.iterdir() if p.name.startswith("donnees.")}
    assert {"donnees.shp", "donnees.shx", "donnees.dbf"} <= names and {"donnees.csv", "donnees.xyz"} <= names
    run_modifier(Writer, {"path": str(target)}, points([5.0], [5.0]))  # réécrit : voisins toujours là
    assert (share / "donnees.csv").read_text(encoding="utf-8") == "a\n1\n"
    assert (share / "donnees.xyz").read_text(encoding="utf-8") == "gardé"
    assert pyogrio.read_info(target)["features"] == 1
    assert leftovers(share) == ([], [])


def test_outside_staged_roots_the_writer_writes_directly(share, tmp_path, monkeypatch):
    other = tmp_path / "local"
    monkeypatch.setenv("ETL_WRITE_ROOTS", os.pathsep.join([str(share), str(other)]))
    run_modifier(Writer, {"path": str(other / "sortie.csv")}, table(a=[1]))
    assert (other / "sortie.csv").exists()
    assert not scratch_of(share).exists()  # aucun dossier de travail : pas de copie inutile


def test_partitioned_output_is_written_directly(share):
    out = share / "parts"
    run_modifier(Writer, {"path": str(out), "format": "parquet", "partition_by": ["d"]}, table(d=["a", "b"], v=[1, 2]))
    assert sorted(p.name for p in out.iterdir()) == ["d=a", "d=b"]
    assert not scratch_of(share).exists()


def test_needs_staging_and_scratch_location(share, monkeypatch):
    assert staging.needs_staging(share / "x" / "y.gpkg") and not staging.needs_staging(share.parent / "ailleurs.gpkg")
    monkeypatch.delenv("ETL_STAGING_DIR")
    monkeypatch.setenv("ETL_CACHE_DIR", "/data/cache")
    assert staging.scratch_root().as_posix().endswith("/data/cache/_staging")


@pytest.mark.skipif(os.name != "nt", reason="chemins UNC propres à Windows")
def test_unc_and_local_paths_are_recognised_as_network_or_not(tmp_path):
    from etl_engine import drives

    bs = chr(92)
    assert drives.is_network_path(bs * 2 + "serveur" + bs + "partage" + bs + "dossier" + bs + "sortie.gpkg") is True
    assert drives.is_network_path("//serveur/partage/sortie.gpkg") is True
    assert drives.is_network_path(str(tmp_path / "sortie.gpkg")) is False
    assert staging.needs_staging(bs * 2 + "serveur" + bs + "partage" + bs + "sortie.gpkg") is True
    assert staging.needs_staging(tmp_path / "sortie.gpkg") is False
