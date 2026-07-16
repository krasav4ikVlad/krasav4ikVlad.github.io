"""MongoDB Change Streams → instant live-feed events.

Watches the raw ``users`` collection and publishes events to the WS hub:

* insert                     → ``registration``
* update of info.transactions → ``topup`` / ``promo`` / ``ref_income``
* update of logs_balance      → ``purchase`` (renewal / device / bypass / gift)
* update of growth.segment    → ``segment``

Array pushes surface in ``updateDescription.updatedFields`` as
``info.transactions.<idx>`` with the new entry as the value, so we normalize
just that entry — no need to fetch the huge full document.

Change streams need a replica set; when unavailable the watcher logs a
warning and degrades gracefully (the dashboard still works, the ticker is
fed by ETL-paced polling instead).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from .config import get_settings
from .db import TX_FLAT
from .etl import ID_FIELDS, extract_user_id
from .normalizer import normalize_debit_entry, normalize_transaction_entry
from .ws import EventHub

log = logging.getLogger("app.change_streams")

# documentKey._id → (user_id, username); update events don't carry the tg id
# when users are keyed by ObjectId, so we resolve it once and remember.
_identity_cache: dict[str, tuple[int | None, str | None]] = {}
_IDENTITY_CACHE_CAP = 5000
_IDENTITY_PROJECTION = {**{f: 1 for f in ID_FIELDS},
                        **{f"info.{f}": 1 for f in ID_FIELDS},
                        **{f"user_data.{f}": 1 for f in ID_FIELDS},
                        "username": 1, "info.username": 1,
                        "user_data.username": 1}


def _nested_username(doc: dict) -> str | None:
    username = doc.get("username")
    for container in ("user_data", "info"):
        if username is None and isinstance(doc.get(container), dict):
            username = doc[container].get("username")
    return str(username) if username is not None else None


async def _resolve_identity(users_coll: Any, change: dict) -> tuple[int | None, str | None]:
    full_doc = change.get("fullDocument") or {}
    doc_key = change.get("documentKey") or {}
    user_id = extract_user_id(full_doc) or extract_user_id(doc_key)
    username = _nested_username(full_doc)
    if user_id is not None:
        return user_id, username

    raw_id = doc_key.get("_id")
    if raw_id is None:
        return None, username
    cache_key = str(raw_id)
    if cache_key in _identity_cache:
        return _identity_cache[cache_key]

    try:
        doc = await users_coll.find_one({"_id": raw_id}, _IDENTITY_PROJECTION)
    except Exception:
        return None, username
    if doc:
        user_id = extract_user_id(doc)
        username = _nested_username(doc)
    if len(_identity_cache) >= _IDENTITY_CACHE_CAP:
        _identity_cache.clear()
    _identity_cache[cache_key] = (user_id, username)
    return user_id, username

_RE_TX_FIELD = re.compile(r"^info\.transactions(?:\.(\d+))?$")
_RE_BAL_FIELD = re.compile(r"^(?:info\.)?logs_balance(?:\.(\d+))?$")


def _entries_from_update(value: Any, indexed: bool) -> list[Any]:
    """Only dotted-index updates ($push) carry an unambiguous new entry.
    A whole-array $set gives no way to tell new entries from old ones —
    emitting the last element would produce phantom events on rewrites, so
    those are skipped (the ETL still picks the data up)."""
    if indexed:
        return [value]
    return []


async def _publish_tx_events(hub: EventHub, user_id: int | None,
                             username: str | None, entries: list[Any]) -> None:
    for entry in entries:
        for tx in normalize_transaction_entry(entry):
            await hub.publish(tx.kind.value if tx.kind.value in
                              ("topup", "ref_income", "promo") else "topup", {
                "user_id": user_id,
                "username": username,
                "amount": tx.amount,
                "source": tx.source,
                "bonus": tx.bonus,
                "promo_code": tx.promo_code,
                "kind": tx.kind.value,
            })


async def _publish_debit_events(hub: EventHub, user_id: int | None,
                                username: str | None, entries: list[Any]) -> None:
    for entry in entries:
        for d in normalize_debit_entry(entry):
            await hub.publish("purchase", {
                "user_id": user_id,
                "username": username,
                "amount": d.amount,
                "kind": d.kind.value,
                "product": d.product,
            })


async def _handle_change(hub: EventHub, change: dict, users_coll: Any) -> None:
    op = change.get("operationType")
    user_id, username = await _resolve_identity(users_coll, change)

    if op == "insert":
        await hub.publish("registration", {
            "user_id": user_id,
            "username": username,
        })
        return

    if op != "update":
        return

    updated = (change.get("updateDescription") or {}).get("updatedFields") or {}
    for field, value in updated.items():
        m = _RE_TX_FIELD.match(field)
        if m:
            await _publish_tx_events(
                hub, user_id, username,
                _entries_from_update(value, m.group(1) is not None))
            continue
        m = _RE_BAL_FIELD.match(field)
        if m:
            await _publish_debit_events(
                hub, user_id, username,
                _entries_from_update(value, m.group(1) is not None))
            continue
        if field == "growth.segment" or field == "growth":
            segment = value if isinstance(value, str) else (
                value.get("segment") if isinstance(value, dict) else None)
            if segment:
                await hub.publish("segment", {
                    "user_id": user_id, "username": username,
                    "segment": segment,
                })


async def _is_replica_set(db: AsyncIOMotorDatabase) -> bool:
    try:
        hello = await db.client.admin.command("hello")
        return bool(hello.get("setName"))
    except Exception:
        # can't tell (connection issue) — assume replica set and keep retrying
        return True


async def watch_users(db: AsyncIOMotorDatabase, hub: EventHub) -> None:
    """Run forever; reconnects on transient errors, degrades to polling when
    change streams are unsupported (standalone mongod)."""
    settings = get_settings()
    users = db[settings.users_collection]
    backoff = 1.0
    while True:
        try:
            async with users.watch(
                [{"$match": {"operationType": {"$in": ["insert", "update"]}}}],
                max_await_time_ms=10_000,
            ) as stream:
                log.info("change stream connected")
                backoff = 1.0
                async for change in stream:
                    try:
                        await _handle_change(hub, change, users)
                    except Exception:
                        log.exception("failed to handle change event")
        except asyncio.CancelledError:
            raise
        except PyMongoError as e:
            if "replica" in str(e).lower() or "$changeStream" in str(e):
                # only demote permanently when the server truly is a
                # standalone; transient replica-set errors must reconnect
                if not await _is_replica_set(db):
                    log.warning("change streams unavailable (standalone "
                                "mongod) — falling back to polling")
                    await poll_fallback(db, hub)
                    return
            log.warning("change stream error, reconnecting",
                        extra={"error": str(e)})
        except Exception:
            log.exception("unexpected change stream failure, reconnecting")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


async def poll_fallback(db: AsyncIOMotorDatabase, hub: EventHub,
                        interval: float = 30.0) -> None:
    """Near-realtime fallback: surface new flat transactions as events.

    Latency is bounded by the ETL interval — good enough to keep the ticker
    alive when the deployment has no replica set.
    """
    last_seen = datetime.now(timezone.utc) - timedelta(minutes=5)
    while True:
        try:
            cursor = db[TX_FLAT].find(
                {"dt": {"$gt": last_seen}, "direction": "credit"},
                sort=[("dt", 1)], limit=50)
            async for row in cursor:
                if row.get("dt"):
                    dt = row["dt"]
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    last_seen = max(last_seen, dt)
                kind = row.get("kind")
                await hub.publish(kind if kind in
                                  ("topup", "ref_income", "promo") else "topup", {
                    "user_id": row.get("user_id"),
                    "username": row.get("username"),
                    "amount": row.get("amount"),
                    "source": row.get("source"),
                    "bonus": row.get("bonus"),
                    "promo_code": row.get("promo_code"),
                    "kind": kind,
                })
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll fallback iteration failed")
        await asyncio.sleep(interval)
