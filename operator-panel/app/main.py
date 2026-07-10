"""Operator support panel — standalone FastAPI service.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8100
(or via PM2, see ecosystem.config.js)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .autoclose import autoclose_loop
from .config import get_settings
from .database import close_client, ensure_indexes
from .routers import actions, audit_log, auth, operators, quick_replies, stats, tickets, users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("operator-panel")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters — refusing to start")
    await ensure_indexes()
    if not (settings.remnawave_base_url and settings.remnawave_token):
        log.warning("Remnawave API is NOT configured — subscription changes will require force_local")
    log.info("Operator panel started (db=%s)", settings.mongo_db)
    autoclose_task = asyncio.create_task(autoclose_loop())
    yield
    autoclose_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await autoclose_task
    await close_client()


app = FastAPI(title="Operator Support Panel", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)  # no public API docs
app.add_middleware(GZipMiddleware, minimum_size=1024)  # большие JSON-ответы жмутся в разы

settings = get_settings()
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(actions.router)
app.include_router(audit_log.router)
app.include_router(operators.router)
app.include_router(tickets.router)
app.include_router(quick_replies.router)
app.include_router(stats.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера. Попробуйте ещё раз или сообщите владельцу."},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


# ------- frontend (static SPA) -------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _assets_version() -> str:
    """Cache-busting token derived from static file mtimes — changes on every deploy."""
    try:
        return str(int(max(p.stat().st_mtime for p in STATIC_DIR.iterdir() if p.is_file())))
    except ValueError:
        return "0"


@app.get("/")
async def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{v}}", _assets_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# Path-form deep links (Telegram-кнопки иногда портят #fragment в URL) —
# отдаём редирект на hash-роут SPA; неавторизованный увидит форму входа.
@app.get("/user/{user_id}")
async def user_deeplink(user_id: int):
    return RedirectResponse(f"/#/user/{user_id}")


@app.get("/ticket/{user_id}")
async def ticket_deeplink(user_id: int):
    return RedirectResponse(f"/#/ticket/{user_id}")
