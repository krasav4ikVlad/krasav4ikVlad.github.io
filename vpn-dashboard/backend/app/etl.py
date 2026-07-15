"""Background ETL: flatten raw user documents into analytics collections.

Every N minutes (APScheduler) the job sweeps the raw ``users`` collection and
rebuilds two flat collections:

* ``transactions_flat`` — one document per normalized credit/debit with
  indexes on ``(dt, kind, source, user_id)``.  All heavy aggregations run
  against this collection, never against the raw denormalized documents.
* ``users_flat`` — a lightweight per-user projection (segment, cohort dates,
  referral stats, devices, funnel timestamps) for cohort/segment analytics.

The job is idempotent: flat transaction ``_id``s are deterministic
(user id + direction + position), re-runs replace documents in place and
stale rows for a user are deleted with a per-user ``DeleteMany``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DeleteMany, ReplaceOne

from .config import get_settings
from .db import ETL_STATE, TX_FLAT, USERS_FLAT
from .normalizer import (
    DebitKind,
    NormalizedDebit,
    NormalizedTransaction,
    TxKind,
    normalize_debits,
    normalize_transactions,
    parse_amount,
    parse_dt,
)

log = logging.getLogger("app.etl")

_BULK_FLUSH = 1000

# Raw-document fields the ETL needs; keeps network traffic sane on big docs.
USER_PROJECTION = {
    "_id": 1, "tg_id": 1, "telegram_id": 1, "user_id": 1,
    "username": 1, "info.username": 1,
    "info.transactions": 1, "logs_balance": 1, "info.logs_balance": 1,
    "growth": 1, "growth_history": 1, "info.growth_history": 1,
    "ref_stats": 1, "referrer_id": 1, "info.referrer_id": 1,
    "campaigns": 1, "info.campaigns": 1,
    "extraDevices": 1, "info.extraDevices": 1,
    "preferred_client": 1, "info.preferred_client": 1,
    "joined_at": 1, "created_at": 1, "info.joined_at": 1,
    "days_to_expire": 1,
    "balance": 1, "info.balance": 1,
    "sub_until": 1, "info.sub_until": 1, "subscription_until": 1,
    "logs": 1,
}


def _tx_flat_id(user_id: Any, direction: str, index: int) -> str:
    return hashlib.sha1(f"{user_id}|{direction}|{index}".encode()).hexdigest()


def _pick(doc: dict, *paths: str) -> Any:
    """Return the first non-None value at any of the dotted paths."""
    for path in paths:
        node: Any = doc
        for part in path.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node is not None:
            return node
    return None


def extract_user_id(doc: dict) -> Optional[int]:
    for candidate in (doc.get("tg_id"), doc.get("telegram_id"),
                      doc.get("user_id"), doc.get("_id")):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)
    return None


def parse_segment_history(raw: Any) -> list[dict]:
    """Normalize ``growth_history`` into ``[{"segment": str, "dt": datetime}]``."""
    if not raw:
        return []
    if isinstance(raw, dict):
        # {"2024-05-01T...": "active_paid", ...} or {"0": {...}}
        items: list[Any] = []
        for key, value in raw.items():
            if isinstance(value, str) and parse_dt(key) is not None:
                items.append({"segment": value, "dt": key})
            else:
                items.append(value)
        raw = items
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[dict] = []
    for entry in raw:
        segment, dt = None, None
        if isinstance(entry, dict):
            segment = entry.get("segment") or entry.get("seg") or entry.get("to")
            dt = parse_dt(entry.get("dt") or entry.get("date") or entry.get("ts")
                          or entry.get("at"))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            a, b = entry[0], entry[1]
            if isinstance(a, str) and parse_dt(a) is None:
                segment, dt = a, parse_dt(b)
            else:
                dt, segment = parse_dt(a), b if isinstance(b, str) else None
        if segment and dt:
            out.append({"segment": str(segment), "dt": dt})
    out.sort(key=lambda x: x["dt"])
    return out


def _parse_extra_devices(raw: Any) -> list[dict]:
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for d in raw:
        if isinstance(d, dict):
            out.append({
                "active": bool(d.get("active", False)),
                "dt": parse_dt(d.get("dt") or d.get("created_at") or d.get("since")),
                "name": str(d.get("name")) if d.get("name") is not None else None,
            })
    return out


def _tx_to_flat(tx: NormalizedTransaction, user_id: int, username: Optional[str],
                index: int, etl_at: datetime) -> dict:
    return {
        "_id": _tx_flat_id(user_id, "credit", index),
        "user_id": user_id,
        "username": username,
        "direction": "credit",
        "dt": tx.dt,
        "amount": tx.amount,
        "kind": tx.kind.value,
        "source": tx.source,
        "bonus": tx.bonus,
        "promo_code": tx.promo_code,
        "ref_meta": tx.ref_meta,
        "payment_id": tx.payment_id,
        "desc": tx.desc,
        "etl_at": etl_at,
    }


def _debit_to_flat(d: NormalizedDebit, user_id: int, username: Optional[str],
                   index: int, etl_at: datetime) -> dict:
    return {
        "_id": _tx_flat_id(user_id, "debit", index),
        "user_id": user_id,
        "username": username,
        "direction": "debit",
        "dt": d.dt,
        "amount": d.amount,
        "kind": d.kind.value,
        "source": None,
        "bonus": 0.0,
        "promo_code": None,
        "ref_meta": None,
        "payment_id": None,
        "desc": d.desc,
        "product": d.product,
        "etl_at": etl_at,
    }


def flatten_user(doc: dict, etl_at: datetime) -> tuple[list[dict], Optional[dict], int]:
    """Turn one raw user document into flat tx rows + a users_flat row.

    Returns ``(tx_rows, user_row, unparsed_count)``; ``user_row`` is ``None``
    when no telegram id could be extracted.
    """
    user_id = extract_user_id(doc)
    if user_id is None:
        return [], None, 0

    username = _pick(doc, "username", "info.username")
    username = str(username) if username is not None else None

    credits, unparsed_c = normalize_transactions(_pick(doc, "info.transactions"))
    debits, unparsed_d = normalize_debits(
        _pick(doc, "logs_balance", "info.logs_balance"))

    rows = [_tx_to_flat(t, user_id, username, i, etl_at)
            for i, t in enumerate(credits)]
    rows += [_debit_to_flat(d, user_id, username, i, etl_at)
             for i, d in enumerate(debits)]

    # ---- users_flat projection ----
    growth = doc.get("growth") if isinstance(doc.get("growth"), dict) else {}
    segment_history = parse_segment_history(
        _pick(doc, "growth_history", "info.growth_history"))

    dated_topups = sorted(
        (t for t in credits if t.kind is TxKind.TOPUP and t.dt and t.amount > 0),
        key=lambda t: t.dt)
    renewal_dts = sorted(d.dt for d in debits
                         if d.kind is DebitKind.RENEWAL and d.dt)
    bypass = [d for d in debits if d.kind is DebitKind.BYPASS]
    device_debits = [d for d in debits if d.kind is DebitKind.DEVICE]
    promo_txs = [t for t in credits if t.kind is TxKind.PROMO]
    all_dts = [t.dt for t in credits if t.dt] + [d.dt for d in debits if d.dt]

    ref_stats_raw = _pick(doc, "ref_stats") or {}
    if not isinstance(ref_stats_raw, dict):
        ref_stats_raw = {}
    ref_stats = {
        "turnover_total": parse_amount(ref_stats_raw.get("turnover_total")) or 0.0,
        "earned_total": parse_amount(
            ref_stats_raw.get("earned_total") or ref_stats_raw.get("earned")) or 0.0,
        "referrals": int(parse_amount(ref_stats_raw.get("referrals")) or 0),
        "paying_referrals": int(
            parse_amount(ref_stats_raw.get("paying_referrals")) or 0),
        "payout_pending": parse_amount(
            ref_stats_raw.get("payout_pending")
            or ref_stats_raw.get("to_payout") or ref_stats_raw.get("balance")) or 0.0,
        "payout_history": ref_stats_raw.get("payout_history")
            if isinstance(ref_stats_raw.get("payout_history"), list) else [],
    }

    campaigns = _pick(doc, "campaigns", "info.campaigns")
    devices = _parse_extra_devices(_pick(doc, "extraDevices", "info.extraDevices"))

    days_to_expire = parse_amount(_pick(doc, "days_to_expire",
                                        "growth.days_to_expire"))

    user_row = {
        "_id": user_id,
        "username": username,
        "username_lower": username.lower() if username else None,
        "joined_at": parse_dt(_pick(doc, "joined_at", "created_at",
                                    "info.joined_at")),
        "segment": growth.get("segment"),
        "segment_history": segment_history,
        "days_to_expire": days_to_expire,
        "sub_until": parse_dt(_pick(doc, "sub_until", "info.sub_until",
                                    "subscription_until")),
        "balance": parse_amount(_pick(doc, "balance", "info.balance")) or 0.0,
        "referrer_id": _pick(doc, "referrer_id", "info.referrer_id"),
        "ref_stats": ref_stats,
        "campaigns": campaigns if isinstance(campaigns, (dict, list)) else None,
        "extra_devices": devices,
        "extra_devices_active": sum(1 for d in devices if d["active"]),
        "preferred_client": _pick(doc, "preferred_client",
                                  "info.preferred_client"),
        # funnel / cohort timestamps derived from normalized history
        "first_topup_at": dated_topups[0].dt if dated_topups else None,
        "topup_total": round(sum(t.amount for t in dated_topups), 2),
        "topup_count": len(dated_topups),
        "bonus_total": round(sum(t.bonus for t in credits), 2),
        "first_sub_at": renewal_dts[0] if renewal_dts else None,
        "second_sub_at": renewal_dts[1] if len(renewal_dts) > 1 else None,
        "last_renewal_at": renewal_dts[-1] if renewal_dts else None,
        "renewals_count": len(renewal_dts),
        "spend_total": round(sum(d.amount for d in debits), 2),
        "bypass_count": len(bypass),
        "bypass_total": round(sum(d.amount for d in bypass), 2),
        "device_spend_total": round(sum(d.amount for d in device_debits), 2),
        "promo_activations": [
            {"code": t.promo_code, "dt": t.dt, "amount": t.amount}
            for t in promo_txs if t.promo_code
        ],
        "last_tx_at": max(all_dts) if all_dts else None,
        "etl_at": etl_at,
    }
    return rows, user_row, unparsed_c + unparsed_d


async def run_etl(db: AsyncIOMotorDatabase) -> dict:
    """One full ETL sweep. Returns run statistics."""
    settings = get_settings()
    started = time.monotonic()
    etl_at = datetime.now(timezone.utc)

    users = db[settings.users_collection]
    tx_flat = db[TX_FLAT]
    users_flat = db[USERS_FLAT]

    tx_ops: list = []
    user_ops: list = []
    stats = {"users": 0, "tx_rows": 0, "unparsed": 0}

    async def flush() -> None:
        nonlocal tx_ops, user_ops
        if tx_ops:
            await tx_flat.bulk_write(tx_ops, ordered=False)
            tx_ops = []
        if user_ops:
            await users_flat.bulk_write(user_ops, ordered=False)
            user_ops = []

    cursor = users.find({}, USER_PROJECTION).batch_size(200)
    async for doc in cursor:
        try:
            rows, user_row, unparsed = flatten_user(doc, etl_at)
        except Exception:
            log.exception("flatten_user failed", extra={"raw_id": str(doc.get("_id"))})
            continue
        if user_row is None:
            continue

        stats["users"] += 1
        stats["tx_rows"] += len(rows)
        stats["unparsed"] += unparsed

        for row in rows:
            tx_ops.append(ReplaceOne({"_id": row["_id"]}, row, upsert=True))
        # drop rows that no longer exist in the source document
        tx_ops.append(DeleteMany({
            "user_id": user_row["_id"],
            "_id": {"$nin": [r["_id"] for r in rows]},
        }))
        user_ops.append(ReplaceOne({"_id": user_row["_id"]}, user_row, upsert=True))

        if len(tx_ops) >= _BULK_FLUSH:
            await flush()

    await flush()

    stats["took_ms"] = int((time.monotonic() - started) * 1000)
    stats["ran_at"] = etl_at
    await db[ETL_STATE].replace_one(
        {"_id": "etl"}, {"_id": "etl", **stats}, upsert=True)
    log.info("etl completed", extra={k: v for k, v in stats.items()
                                     if k != "ran_at"})
    return stats
