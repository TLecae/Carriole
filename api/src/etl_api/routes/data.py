"""Inspecteur : tables filtrables côté serveur, statistiques, export, tuiles vectorielles et raster."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import pyarrow as pa
from etl_engine import formats
from etl_engine.data import with_crs
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from etl_api import data_service as ds
from etl_api import raster_service as rs

router = APIRouter(tags=["inspecteur"])


def get_duck(request: Request) -> ds.Duck:
    return request.app.state.duck


Duck = Annotated[ds.Duck, Depends(get_duck)]


def port_of(run: str, node: str, port: str, duck: Duck) -> ds.Port:
    return ds.open_port(duck, run, node, port)


def raster_port_of(run: str, node: str, port: str, duck: Duck) -> ds.Port:
    """Les rasters sont lus dans le port lui-même : pas besoin de la vue."""
    return ds.open_port(duck, run, node, port, prepare=False)


PortDep = Annotated[ds.Port, Depends(port_of)]
RasterPortDep = Annotated[ds.Port, Depends(raster_port_of)]


class ValuesIn(BaseModel):
    column: str
    query: ds.Query = ds.Query()
    limit: int = 50


class LocateIn(BaseModel):
    query: ds.Query = ds.Query()
    fid: int


@router.get("/data/{run}/{node}/{port}/info")
def info(port: PortDep) -> dict[str, Any]:
    return ds.info(port)


@router.post("/data/{run}/{node}/{port}/rows")
def rows(port: PortDep, query: ds.Query) -> dict[str, Any]:
    return ds.rows(port, query)


@router.get("/data/{run}/{node}/{port}/stats")
def stats(port: PortDep, geometry: bool = False) -> dict[str, Any]:
    return ds.stats(port, with_validity=geometry)


@router.post("/data/{run}/{node}/{port}/values")
def values(port: PortDep, body: ValuesIn) -> list[dict[str, Any]]:
    return ds.values(port, body.column, body.query, min(body.limit, 500))


@router.post("/data/{run}/{node}/{port}/locate")
def locate(port: PortDep, body: LocateIn) -> dict[str, int | None]:
    return {"index": ds.locate(port, body.query, body.fid)}


@router.get("/data/{run}/{node}/{port}/feature/{fid}")
def feature(port: PortDep, fid: int) -> dict[str, Any]:
    return ds.feature(port, fid)


@router.get("/data/{run}/{node}/{port}/raster/{fid}")
def raster_info(port: RasterPortDep, fid: int) -> dict[str, Any]:
    return rs.info(port, fid)


@router.get("/data/{run}/{node}/{port}/raster/{fid}/pixel")
def raster_pixel(port: RasterPortDep, fid: int, lon: float, lat: float) -> dict[str, Any]:
    return rs.pixel(port, fid, lon, lat)


_EXPORTS = {"csv": ("csv", ".csv"), "gpkg": ("gpkg", ".gpkg"), "parquet": ("parquet", ".parquet")}


@router.post("/data/{run}/{node}/{port}/export")
def export(
    port: PortDep, query: ds.Query, request: Request, format: Literal["csv", "gpkg", "parquet"] = "csv"
) -> FileResponse:
    """Exporte la vue filtrée et triée (toutes les lignes qui correspondent, pas seulement la page affichée)."""
    fmt, suffix = _EXPORTS[format]
    folder = request.app.state.settings.cache_dir / "_exports" / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{port.node}_{port.name}{suffix}"
    writer = formats.open_writer(fmt, str(target), **({"layer": port.name} if fmt == "gpkg" else {}))
    count = 0
    try:
        for batch in ds.export_batches(port, query.model_copy(update={"offset": 0})):
            table = with_crs(pa.Table.from_batches([batch]), port.crs)
            writer.write(table)
            count += table.num_rows
    finally:
        writer.close()
    if count == 0:
        _cleanup(folder)
        raise ds.DataError("aucune entité à exporter")
    return FileResponse(target, filename=target.name, background=BackgroundTask(_cleanup, folder))


def _cleanup(folder: Path) -> None:
    for f in folder.glob("*"):
        f.unlink(missing_ok=True)
    folder.rmdir()


def decode_query(q: str | None) -> ds.Query:
    """Filtre transmis dans l'URL des tuiles : JSON encodé en base64 URL-safe."""
    if not q:
        return ds.Query()
    try:
        return ds.Query.model_validate(json.loads(base64.urlsafe_b64decode(q + "=" * (-len(q) % 4))))
    except (ValueError, ValidationError):
        raise ds.DataError("paramètre q invalide", 400) from None


tiles = APIRouter(tags=["tuiles"])


@tiles.get("/tiles/{run}/{node}/{port}/{z}/{x}/{y}.pbf")
async def tile(
    port: PortDep,
    request: Request,
    z: int,
    x: int,
    y: int,
    q: str | None = None,
    color: str | None = None,
) -> Response:
    query = decode_query(q)
    # La carte annule les tuiles qu'elle ne veut plus (autre port, carte fermée, zoom passé) : celles qui attendent
    # encore leur tour sont abandonnées, sans retarder celles du port affiché.
    gone = threading.Event()

    async def watch() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)
        gone.set()

    watcher = asyncio.ensure_future(watch())
    try:
        data = await run_in_threadpool(ds.tile, port, z, x, y, query, color, gone.is_set)
    finally:
        watcher.cancel()
    # Le contenu d'une URL ne change pas (run figé ; filtre et couleur dans l'URL) : le navigateur le garde. Tuile
    # gardée compressée sur le disque, envoyée telle quelle.
    headers = {"Cache-Control": "public, max-age=86400, immutable", "Content-Encoding": "gzip"}
    return Response(data, media_type="application/x-protobuf", headers=headers)


@tiles.get("/tiles/raster/{run}/{node}/{port}/{fid}/{z}/{x}/{y}.png")
def raster_tile(
    port: RasterPortDep,
    fid: int,
    z: int,
    x: int,
    y: int,
    bands: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    palette: str = "viridis",
    resampling: str = "bilinear",
) -> Response:
    data = rs.tile(port, fid, z, x, y, bands, vmin, vmax, palette, resampling)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})
