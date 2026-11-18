"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .agent import ChatService, Runtime, registry
from .agent.mcp_server import build_server
from .config import Settings, get_settings
from .floorsources import FloorSync, load_floor_sources
from .importer import apply_scan, run_scan
from .models import Base
from .providers import Poller, load_providers

log = logging.getLogger("labkeeper")


class ImportService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.last_summary: dict[str, Any] | None = None
        self.watching = False
        self._task: asyncio.Task | None = None

    async def run(self, actor: str = "importer") -> dict[str, Any]:
        async with self.lock:
            result = await asyncio.to_thread(run_scan, self.settings)
            summary = await db.run_in_session(lambda s: apply_scan(s, result, self.settings, actor))
            self.last_summary = summary.as_dict()
            log.info(
                "import: %s testbeds, %s devices, %s invalid files",
                summary.testbeds, summary.devices, summary.invalid,
            )
            return self.last_summary

    async def _watch(self) -> None:
        from watchfiles import awatch

        roots = [str(r) for r in self.settings.testbed_roots() if r.exists()]
        if not roots:
            return
        self.watching = True
        try:
            async for _changes in awatch(
                *roots, debounce=1500, watch_filter=lambda _c, p: p.endswith((".yaml", ".yml"))
            ):
                try:
                    await self.run("file watcher")
                except Exception:  # pragma: no cover
                    log.exception("re-import after file change failed")
        finally:
            self.watching = False

    def start_watching(self) -> None:
        if self.settings.testbeds.watch and self._task is None:
            self._task = asyncio.create_task(self._watch(), name="testbed-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


@dataclass
class AppState:
    settings: Settings
    poller: Poller
    importer: ImportService
    runtime: Runtime
    chat: ChatService
    floor_sync: FloorSync
    extras: list[Any] = field(default_factory=list)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine(settings.resolved_database_url)
    Base.metadata.create_all(db.engine())
    for col in db.ensure_columns(db.engine(), Base.metadata):
        log.info("database upgraded: added column %s", col)

    providers = load_providers(settings)
    poller = Poller(providers)
    importer = ImportService(settings)
    floor_sync = FloorSync(load_floor_sources(settings))
    runtime = Runtime(settings=settings, poller=poller, import_now=importer.run, floor_sync=floor_sync)
    chat = ChatService(settings.agent, registry, runtime)
    mcp_server = build_server(registry, runtime)
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/", host=settings.host)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        st: AppState = app.state.lk
        try:
            await importer.run("startup")
        except Exception:
            log.exception("initial import failed")
        if settings.demo:
            from .demo import seed_demo, start_demo_consoles

            await db.run_in_session(lambda s: seed_demo(s, settings))
            st.extras.extend(await start_demo_consoles())
        importer.start_watching()
        poller.start()
        floor_sync.start()
        async with mcp_server.session_manager.run():
            yield
        await poller.stop()
        await floor_sync.stop()
        await importer.stop()
        for server in st.extras:
            server.close()

    app = FastAPI(
        title="Labkeeper",
        version=__version__,
        description="Testbed inventory, live device status, bookings, racks and floor plans.",
        lifespan=lifespan,
    )
    app.state.lk = AppState(
        settings=settings, poller=poller, importer=importer, runtime=runtime, chat=chat, floor_sync=floor_sync
    )

    export_origins = {
        o.rstrip("/")
        for src in floor_sync.sources.values()
        for o in ((src.browser_export().origins if src.browser_export() else []) or [])
    }

    @app.middleware("http")
    async def export_cors(request: Request, call_next):
        """Let floor-source bookmarklets POST exports from their own site, on this one endpoint only."""
        origin = request.headers.get("origin", "").rstrip("/")
        if request.url.path != "/api/v1/floorplans/extract" or origin not in export_origins:
            return await call_next(request)
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": f"Content-Type, {settings.auth.user_header}",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }
        if request.headers.get("access-control-request-private-network") == "true":
            headers["Access-Control-Allow-Private-Network"] = "true"
        if request.method == "OPTIONS":
            from fastapi.responses import Response

            return Response(status_code=204, headers=headers)
        response = await call_next(request)
        response.headers.update(headers)
        return response

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from .api.assistant import router as assistant_router
    from .api.floorplans import router as floor_router
    from .api.inventory import router as inventory_router
    from .api.reservations import router as reservations_router
    from .api.testbeds import router as testbed_router

    for r in (inventory_router, testbed_router, reservations_router, floor_router, assistant_router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/mcp", mcp_app)

    dist = settings.frontend_dist or _default_dist()
    if dist and (dist / "index.html").is_file():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request):
            if path.startswith(("api/", "mcp")):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def _default_dist() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parent / "static", here.parents[2] / "frontend" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None
