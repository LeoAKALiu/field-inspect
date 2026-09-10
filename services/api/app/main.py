"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import ws as ws_module
from .config import DEMO_DIR, Settings, assert_durable_paths
from .db import Database
from .errors import DEFAULT_CODES
from .routers import datasources, devices, events, health, imports, live, scenes, tasks, instruments
from .access import allowed, session_cookie
import os
from .run_bundle import ensure_import_directories, recover_incomplete_imports
from .seed import seed_if_empty
from .simulation import SimulationEngine
from .writer_lock import ImportWriterLock, WriterLockHeld, assert_single_api_process


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    assert_durable_paths(settings)
    assert_single_api_process()
    db = Database(settings.db_path)
    engine = SimulationEngine(db, tick_ms=settings.sim_tick_ms, seed=settings.seed)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_schema()
        seed_if_empty(db, DEMO_DIR)
        ensure_import_directories(settings.import_root)
        try:
            with ImportWriterLock(settings.import_root):
                recover_incomplete_imports(db, settings.import_root)
        except WriterLockHeld:
            pass
        yield
        await engine.stop()
        db.close()

    app = FastAPI(
        title="Field Inspect API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.engine = engine

    @app.middleware("http")
    async def access_boundary(request: Request, call_next):
        if request.url.path != "/api/health" and not allowed(request.scope, request.headers):
            return JSONResponse(status_code=401, content={"error": {
                "code": "authentication_required", "message": "需要授权；默认仅允许本机访问。"}})
        return await call_next(request)

    @app.post("/api/access/login")
    async def login(request: Request):
        secret = os.environ.get("ASTRA_API_TOKEN", "")
        response = JSONResponse({"authenticated": True, "identity_model": "shared_operator_secret"})
        if secret:
            response.set_cookie("astra_session", session_cookie(secret), httponly=True,
                                secure=request.url.scheme == "https", samesite="strict", max_age=8*3600)
        return response

    @app.post("/api/access/logout")
    async def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("astra_session")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            error = detail
        else:
            error = {
                "code": DEFAULT_CODES.get(exc.status_code, "http_error"),
                "message": str(detail),
            }
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    for module in (health, scenes, tasks, events, devices, datasources, live, imports, instruments):
        app.include_router(module.router, prefix="/api")
    app.include_router(ws_module.router)
    return app


app = create_app()
