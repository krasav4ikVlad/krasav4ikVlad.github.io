"""MongoDB (Motor) connection management + index bootstrap."""
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import get_settings

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(
            settings.mongo_url,
            serverSelectionTimeoutMS=5000,
            uuidRepresentation="standard",
        )
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_settings().mongo_db]


async def _safe_create_index(collection, keys, **opts) -> None:
    """create_index that tolerates an equivalent index already existing under a
    different name / options (the bot may have created its own indexes on `users`).
    IndexOptionsConflict=85, IndexKeySpecsConflict=86."""
    from pymongo.errors import OperationFailure

    try:
        await collection.create_index(keys, **opts)
    except OperationFailure as e:
        if e.code in (85, 86):
            log.info("Index on %s %s already exists (bot-owned), skipping", collection.name, keys)
        else:
            raise


async def ensure_indexes() -> None:
    """Create the indexes the panel relies on. Idempotent, tolerant of bot-owned indexes."""
    settings = get_settings()
    db = get_db()

    users = db[settings.users_collection]
    await _safe_create_index(users, "user_data.user_id")
    await _safe_create_index(users, "user_data.username")
    await _safe_create_index(users, "info.email", sparse=True)

    audit = db[settings.audit_collection]
    await _safe_create_index(audit, [("timestamp", -1)])
    await _safe_create_index(audit, [("operator_id", 1), ("timestamp", -1)])
    await _safe_create_index(audit, [("target_user_id", 1), ("timestamp", -1)])
    await _safe_create_index(audit, [("action", 1), ("timestamp", -1)])

    operators = db[settings.operators_collection]
    await _safe_create_index(operators, "login", unique=True)

    await _safe_create_index(users, "info.support.thread_id", sparse=True)
    await _safe_create_index(users, [("info.support.status", 1), ("info.support.pending_at", -1)])
    messages = db[settings.support_messages_collection]
    await _safe_create_index(messages, [("user_id", 1), ("timestamp", -1)])

    log.info("MongoDB indexes ensured")


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
