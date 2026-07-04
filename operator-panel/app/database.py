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


async def ensure_indexes() -> None:
    """Create the indexes the panel relies on. Idempotent."""
    settings = get_settings()
    db = get_db()

    users = db[settings.users_collection]
    await users.create_index("user_data.user_id", name="op_panel_user_id")
    await users.create_index("user_data.username", name="op_panel_username")
    await users.create_index("info.email", name="op_panel_email", sparse=True)

    audit = db[settings.audit_collection]
    await audit.create_index([("timestamp", -1)], name="op_audit_ts")
    await audit.create_index([("operator_id", 1), ("timestamp", -1)], name="op_audit_operator")
    await audit.create_index([("target_user_id", 1), ("timestamp", -1)], name="op_audit_target")
    await audit.create_index([("action", 1), ("timestamp", -1)], name="op_audit_action")

    operators = db[settings.operators_collection]
    await operators.create_index("login", unique=True, name="op_login_unique")

    log.info("MongoDB indexes ensured")


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
