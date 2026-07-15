"""MongoDB connection and collection helpers."""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import get_settings

log = logging.getLogger("app.db")

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=8000,
            tz_aware=True,
        )
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_settings().mongo_db]


# Collection names produced/used by the dashboard
TX_FLAT = "transactions_flat"
USERS_FLAT = "users_flat"
ETL_STATE = "etl_state"
ALERTS = "alerts"
ACTIVITY = "activity_stats"
PAYMENTS_FLAT = "payments_flat"


async def ensure_indexes() -> None:
    """Create indexes for the flat analytics collections (idempotent)."""
    db = get_db()
    tx = db[TX_FLAT]
    await tx.create_index([("dt", 1), ("kind", 1), ("source", 1), ("user_id", 1)],
                          name="dt_kind_source_user")
    await tx.create_index([("kind", 1), ("dt", -1)], name="kind_dt")
    await tx.create_index([("source", 1), ("dt", -1)], name="source_dt")
    await tx.create_index([("user_id", 1), ("dt", -1)], name="user_dt")
    await tx.create_index([("direction", 1), ("dt", -1)], name="direction_dt")
    await tx.create_index([("promo_code", 1), ("dt", -1)], name="promo_dt",
                          partialFilterExpression={"promo_code": {"$type": "string"}})

    uf = db[USERS_FLAT]
    await uf.create_index([("joined_at", 1)], name="joined_at")
    await uf.create_index([("segment", 1)], name="segment")
    await uf.create_index([("username_lower", 1)], name="username_lower")
    await uf.create_index([("ref_stats.turnover_total", -1)], name="ref_turnover")

    await db[ALERTS].create_index([("created_at", -1)], name="created_at")
    await db[ALERTS].create_index([("key", 1), ("created_at", -1)], name="key_created")

    pf = db[PAYMENTS_FLAT]
    await pf.create_index([("dt", -1)], name="dt")
    await pf.create_index([("source", 1), ("status", 1), ("dt", -1)],
                          name="source_status_dt")
    await pf.create_index([("user_id", 1), ("dt", -1)], name="user_dt")
    log.info("indexes ensured")


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
