"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from .processing_jobs import Worker

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import ws as ws_module
from .config import DEMO_DIR, Settings, assert_durable_paths
from .db import Database
from .errors import DEFAULT_CODES
from .routers import datasources, devices, events, health, imports, live, scenes, tasks, instruments
from .access import identity, permitted, login_cookie, _current_identity
from datetime import datetime, timezone
import os
from .run_bundle import ensure_import_directories, recover_incomplete_imports
from .seed import seed_if_empty
from .simulation import SimulationEngine
from .writer_lock import ImportWriterLock, WriterLockHeld, assert_single_api_process


def create_app(settings: Settings | None = None, *, schema_only: bool = False) -> FastAPI:
    settings = settings or Settings.from_env()
    assert_durable_paths(settings)
    assert_single_api_process()
    db = None if schema_only else Database(settings.db_path)
    engine = None if schema_only else SimulationEngine(db, tick_ms=settings.sim_tick_ms, seed=settings.seed)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db is None or engine is None:
            raise RuntimeError("Schema export application cannot serve requests")
        db.init_schema()
        seed_if_empty(db, DEMO_DIR)
        ensure_import_directories(settings.import_root)
        try:
            with ImportWriterLock(settings.import_root):
                recover_incomplete_imports(db, settings.import_root)
        except WriterLockHeld:
            pass
        worker = Worker(db, settings.import_root)
        worker.start()
        try:
            yield
        finally:
            await asyncio.to_thread(worker.stop)
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
        principal = identity(request.scope, request.headers)
        if request.url.path != "/api/health" and not permitted(principal,request.scope):
            return JSONResponse(status_code=403 if principal else 401, content={"error": {
                "code": "access_denied", "message": "需要有效身份及对应操作权限。"}})
        request.state.identity = principal
        token = _current_identity.set(principal)
        try:
            response = await call_next(request)
            if db is not None and principal and request.method not in {"GET","HEAD","OPTIONS"}:
                db.execute("INSERT INTO access_audit(actor,role,method,path,status,created_at) VALUES(?,?,?,?,?,?)",
                           (principal["id"],principal["role"],request.method,request.url.path,response.status_code,datetime.now(timezone.utc).isoformat()))
            return response
        finally:
            _current_identity.reset(token)

    @app.post("/api/access/login")
    async def login(request: Request):
        principal = request.state.identity
        response = JSONResponse({"authenticated": True, "identity": principal})
        try:
            cookie = login_cookie(principal)
        except (OSError, ValueError, StopIteration, KeyError) as exc:
            raise HTTPException(401, "Credential changed; authenticate again") from exc
        if cookie:
            response.set_cookie("astra_session",cookie,httponly=True,secure=request.url.scheme=="https",samesite="strict",max_age=8*3600)
        return response

    @app.get("/api/access/me")
    async def current_user(request: Request):
        return request.state.identity

    @app.get("/api/access/audit")
    async def audit_log(cursor: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        rows = db.query_all("SELECT * FROM access_audit WHERE seq>? ORDER BY seq LIMIT ?", (cursor,limit+1))
        return {"items":[dict(row) for row in rows[:limit]], "next_cursor":rows[limit-1]["seq"] if len(rows)>limit else None}

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


app = create_app(schema_only=os.environ.get("FIELD_SCHEMA_EXPORT") == "1")
