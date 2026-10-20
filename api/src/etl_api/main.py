"""Application FastAPI : flux, runs, événements. Elle crée des jobs et relaie des événements, rien de plus."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from etl_engine import __version__, i18n
from etl_engine import components as engine_components
from etl_engine import connections as engine_connections
from etl_engine.registry import Registry
from etl_store import ConnectionStore, Settings, open_store
from etl_store.purge import release_cache
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from etl_api.cache_hold import Holds, sweep_loop
from etl_api.data_service import DataError, Duck
from etl_api.deps import request_lang
from etl_api.routes import (
    analysis,
    app_settings,
    assistant,
    components,
    connections,
    data,
    files,
    flows,
    modifiers,
    preferences,
    runs,
    schedules,
)
from etl_api.security import LocalGuard


async def _sweep(store, stale_seconds: float) -> None:
    """Marque « interrompus » les runs dont le worker a disparu, même si aucun worker n'est plus là pour le faire."""
    while True:
        with contextlib.suppress(Exception):  # le balayage réessaiera
            await asyncio.to_thread(store.interrupt_stale_runs, stale_seconds)
        await asyncio.sleep(max(stale_seconds / 3, 2))


class Frontend(StaticFiles):
    """Interface statique. Une écriture (PUT, POST…) qui n'a trouvé aucune route d'API est un 404, pas un 405."""

    async def get_response(self, path, scope):
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def frontend_dir(settings: Settings) -> Path | None:
    """Interface compilée : `ETL_FRONTEND_DIR` (installation), sinon `frontend/dist` du dépôt (développement)."""
    for candidate in (settings.frontend_dir, Path(__file__).resolve().parents[3] / "frontend" / "dist"):
        if candidate and (candidate / "index.html").is_file():
            return candidate
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.store = open_store(settings)
        app.state.connections = ConnectionStore(app.state.store, settings.secret_key)
        engine_connections.install_provider(app.state.connections.provider(), key="store")
        if provider := app.state.store.components_provider():
            engine_components.install_provider(provider, key="store")
        app.state.registry = Registry.discover(settings.plugins_dir)
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        app.state.duck = Duck(settings.cache_dir)
        app.state.holds = Holds(settings.cache_hold_ttl)
        tasks = [asyncio.create_task(_sweep(app.state.store, settings.stale_seconds))]
        if settings.cache_policy == "session":  # poste de bureau : cache gardé tant que son flux est ouvert
            tasks.append(asyncio.create_task(sweep_loop(app.state.store, settings.cache_dir, app.state.holds)))
        yield
        for task in tasks:
            task.cancel()
        if settings.cache_policy == "session":  # moteur arrêté : plus de flux ouvert, seuls les runs en cours gardent
            with contextlib.suppress(Exception):
                release_cache(app.state.store, settings.cache_dir, set())

    app = FastAPI(title="Carriole", version=__version__, lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, str]:  # sur la boucle : répond même si toutes les requêtes lourdes sont occupées
        return {"status": "ok"}

    @app.post("/api/shutdown", include_in_schema=False)
    def shutdown(request: Request) -> dict[str, str]:  # protégé par le jeton comme le reste de l'API
        stop = getattr(request.app.state, "request_shutdown", None)
        if stop is None:
            raise HTTPException(status_code=501, detail="arrêt possible seulement sous « etl serve »")
        stop()
        return {"status": "stopping"}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        # « flow_storage » : l'interface sait si les flux sont des fichiers (poste de bureau) ou dans la base (serveur)
        return {"version": __version__, "flow_storage": settings.flow_storage}

    for module in (
        modifiers,
        flows,
        analysis,
        runs,
        data,
        connections,
        schedules,
        components,
        files,
        preferences,
        app_settings,
        assistant,
    ):
        app.include_router(module.router, prefix="/api")
    app.include_router(data.tiles)  # /tiles/… (hors du préfixe /api)

    @app.exception_handler(DataError)
    async def data_error(request: Request, exc: DataError) -> JSONResponse:
        return JSONResponse({"detail": i18n.tr(str(exc), request_lang(request))}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Réponses d'erreur dans la langue de la requête (le code lève des messages français)."""
        detail = i18n.translate_json(exc.detail, request_lang(request), frozenset({"message"}))
        if isinstance(detail, str):
            detail = i18n.tr(detail, request_lang(request))
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    web = frontend_dir(settings)
    if web:  # monté en dernier : les routes /api et /tiles passent avant
        app.mount("/", Frontend(directory=web, html=True), name="frontend")
    app.add_middleware(LocalGuard, token=settings.api_token, hosts=settings.allowed_hosts)
    return app


app = create_app()
