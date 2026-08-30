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
            # Mongo на другом сервере: сжатие сильно ускоряет передачу больших
            # документов (у пользователей огромные массивы logs/transactions)
            compressors="zlib",
        )
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_settings().mongo_db]


# ---------------------------------------------------------------- quick replies

_qr_client: AsyncIOMotorClient | None = None
_qr_colname: str | None = None


def _qr_db() -> AsyncIOMotorDatabase:
    """База быстрых ответов: по умолчанию основная, но бот может держать их
    в другом Mongo/базе — тогда QUICK_REPLIES_MONGO_URL / QUICK_REPLIES_DB."""
    global _qr_client
    settings = get_settings()
    url = settings.quick_replies_mongo_url or settings.mongo_url
    if url == settings.mongo_url:
        client = get_client()
    else:
        if _qr_client is None:
            _qr_client = AsyncIOMotorClient(
                url, serverSelectionTimeoutMS=5000,
                uuidRepresentation="standard", compressors="zlib")
        client = _qr_client
    return client[settings.quick_replies_db or settings.mongo_db]


async def quick_replies_col():
    """Коллекция быстрых ответов с умным выбором имени: в боте коллекция
    исторически называется 'support_quick_replies ' (с хвостовым пробелом),
    поэтому если точного имени нет или оно пустое — берём вариант, совпадающий
    с точностью до пробелов и содержащий больше документов. Выбор кэшируется."""
    global _qr_colname
    db = _qr_db()
    settings = get_settings()
    want = settings.quick_replies_collection
    if _qr_colname is None:
        chosen = want
        try:
            names = await db.list_collection_names()
            candidates = [n for n in names if n == want or n.strip() == want.strip()]
            best_count = -1
            for name in sorted(candidates, key=lambda n: n != want):  # точное имя первым
                count = await db[name].count_documents({})
                if count > best_count:
                    chosen, best_count = name, count
        except Exception as e:
            log.warning("quick replies collection discovery failed: %s", e)
        if chosen != want:
            log.info("quick replies: using collection %r (exact %r is missing/empty)",
                     chosen, want)
        _qr_colname = chosen
    return db[_qr_colname]


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
    # текстовый индекс — поиск похожих случаев для ИИ-помощника
    await _safe_create_index(messages, [("text", "text")])

    # журнал денег и платежи ведёт бот; индексы могут уже существовать — _safe
    balance_log = db[settings.balance_log_collection]
    await _safe_create_index(balance_log, [("user_id", 1), ("at", -1)])
    payments = db[settings.payments_collection]
    await _safe_create_index(payments, [("user_id", 1), ("created_at", -1)])

    reviews = db[settings.reviews_collection]
    await _safe_create_index(reviews, [("operator_login", 1), ("user_id", 1)], unique=True)
    await _safe_create_index(reviews, [("operator_login", 1), ("acked", 1), ("created_at", -1)])

    log.info("MongoDB indexes ensured")


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
