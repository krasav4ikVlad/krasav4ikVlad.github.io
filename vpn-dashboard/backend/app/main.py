"""FastAPI application: wiring, lifespan, jobs, middleware."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .alerts import AlertEngine
from .auth import require_admin
from .cache import get_cache
from .change_streams import watch_users
from .config import get_settings
from .db import ETL_STATE, close_db, ensure_indexes, get_db
from .etl import run_etl
from .logging_setup import setup_logging
from .remnawave import get_remnawave
from .ws import hub

log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    db = get_db()

    try:
        await ensure_indexes()
    except Exception:
        log.exception("could not ensure indexes (mongo unreachable?)")

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def etl_job() -> None:
        try:
            await run_etl(get_db())
        except Exception:
            log.exception("etl run failed")

    async def alerts_job() -> None:
        try:
            await AlertEngine(get_db()).run_all()
        except Exception:
            log.exception("alerts run failed")

    scheduler.add_job(etl_job, "interval",
                      minutes=settings.etl_interval_minutes,
                      max_instances=1, coalesce=True, id="etl")
    scheduler.add_job(alerts_job, "interval",
                      minutes=settings.alerts_interval_minutes,
                      max_instances=1, coalesce=True, id="alerts")
    scheduler.start()

    # first ETL sweep right away so a fresh deployment has data
    initial_etl = asyncio.create_task(etl_job())
    watcher = asyncio.create_task(watch_users(db, hub))

    log.info("startup complete", extra={"etl_minutes": settings.etl_interval_minutes})
    try:
        yield
    finally:
        watcher.cancel()
        initial_etl.cancel()
        scheduler.shutdown(wait=False)
        await get_remnawave().close()
        await get_cache().close()
        await close_db()
        log.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VPN Analytics Dashboard", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={
                "path": request.url.path, "method": request.method})
            return JSONResponse({"detail": "Internal server error"}, 500)
        ms = int((time.monotonic() - started) * 1000)
        if not request.url.path.endswith("/health"):
            log.info("request", extra={
                "path": request.url.path, "method": request.method,
                "status": response.status_code, "ms": ms})
        return response

    from .routers import auth_router, ws_router
    from .routers import (overview, revenue, users, referrals, promos,
                          product, infra, alerts_router, experiments)

    app.include_router(auth_router.router, prefix="/api")
    app.include_router(ws_router.router, prefix="/api")
    for module in (overview, revenue, users, referrals, promos, product,
                   infra, alerts_router, experiments):
        app.include_router(module.router, prefix="/api",
                           dependencies=[require_admin])

    @app.get("/api/health")
    async def health():
        db = get_db()
        mongo_ok = True
        etl_state = None
        try:
            etl_state = await db[ETL_STATE].find_one({"_id": "etl"})
        except Exception:
            mongo_ok = False
        return {
            "ok": mongo_ok,
            "mongo": mongo_ok,
            "ws_clients": hub.client_count,
            "etl_last_run": (etl_state or {}).get("ran_at"),
            "etl_tx_rows": (etl_state or {}).get("tx_rows"),
        }

    return app


app = create_app()
