"""Точка входа API платёжных вебхуков: uvicorn app.main_api:app"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.core.config import Config
from app.core.container import Container
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.from_env()
    setup_logging(config.log_level)

    container = Container.build(config)
    await container.startup()
    app.state.container = container
    yield


app = FastAPI(title='RS VPN API', lifespan=lifespan)
app.include_router(webhooks_router)


@app.get('/health')
async def health():
    return {'status': 'ok'}
