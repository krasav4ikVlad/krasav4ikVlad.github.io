"""Background ETL: flatten raw user documents into analytics collections.

Every N minutes (APScheduler) the job sweeps the raw ``users`` collection and
rebuilds two flat collections:

* ``transactions_flat`` — one document per normalized credit/debit with
  indexes on ``(dt, kind, source, user_id)``.  All heavy aggregations run
  against this collection, never against the raw denormalized documents.
* ``users_flat`` — a lightweight per-user projection (segment, cohort dates,
  referral stats, devices, funnel timestamps) for cohort/segment analytics.

The job is idempotent: flat transaction ``_id``s are deterministic
(user id + direction + position), re-runs replace documents in place, and
rows not re-stamped by the current sweep (shrunk lists, deleted users) are
purged afterwards by their ``etl_at`` mark.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne

from .config import get_settings
from .db import ACTIVITY, ETL_STATE, PAYMENTS_FLAT, TX_FLAT, USERS_FLAT
from .normalizer import (
    DebitKind,
    NormalizedDebit,
    NormalizedTransaction,
    TxKind,
    debit_kind_from_desc,
    normalize_debits,
    normalize_payment_webhook,
    normalize_transactions,
    parse_amount,
    parse_dt,
)

log = logging.getLogger("app.etl")

_BULK_FLUSH = 1000

# Telegram-id candidates, in priority order; the raw ``_id`` is the last
# resort (many deployments key users by ObjectId with the tg id in a field).
ID_FIELDS = ("tg_id", "telegram_id", "user_id", "tgid", "chat_id",
             "id", "uid", "tg")

# per-user cap so one hyperactive user can't skew ETL cost / traffic
_MAX_LOG_DTS_PROJ = 500

# Raw-document fields the ETL needs; keeps network traffic sane on big docs.
USER_PROJECTION = {
    "_id": 1, **{f: 1 for f in ID_FIELDS},
    **{f"info.{f}": 1 for f in ID_FIELDS},
    **{f"user_data.{f}": 1 for f in ID_FIELDS},
    "username": 1, "info.username": 1, "user_data.username": 1,
    "user_data.date_joined": 1, "user_data.referrer": 1, "user_data.utm": 1,
    "vpn": 1, "info.ref_stats": 1,
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
    # only the tail is needed for the activity heatmap — big win over WAN
    "logs": {"$slice": -_MAX_LOG_DTS_PROJ},
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
    candidates = [doc.get(f) for f in ID_FIELDS]
    # nested containers seen in the wild: info.*, user_data.* (aiogram's
    # serialized Telegram user object carries the id as user_data.id)
    for container in ("user_data", "info"):
        nested = doc.get(container)
        if isinstance(nested, dict):
            candidates.extend(nested.get(f) for f in ID_FIELDS)
    candidates.append(doc.get("_id"))
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, float) and candidate.is_integer():
            return int(candidate)
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
            # production shape: {"from": ..., "to": ..., "changed_at": Date}
            segment = entry.get("segment") or entry.get("seg") or entry.get("to")
            dt = parse_dt(entry.get("dt") or entry.get("changed_at")
                          or entry.get("date") or entry.get("ts")
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

    username = _pick(doc, "username", "user_data.username", "info.username")
    username = str(username) if username is not None else None

    credits_all, unparsed_c = normalize_transactions(
        _pick(doc, "info.transactions"))
    debits, unparsed_d = normalize_debits(
        _pick(doc, "logs_balance", "info.logs_balance"))

    # The current bot writes spends as NEGATIVE rows in info.transactions
    # («Продление подписки», «Плата за устройства»); logs_balance is legacy.
    # Route them to the debit side so renewals/devices/bypass are counted.
    credits = []
    for t in credits_all:
        if (t.amount < 0
                and t.kind not in (TxKind.REF_INCOME, TxKind.PROMO,
                                   TxKind.REFUND)):
            kind, product = debit_kind_from_desc(t.desc)
            debits.append(NormalizedDebit(
                amount=abs(t.amount), dt=t.dt, kind=kind,
                product=product, desc=t.desc, raw=t.raw))
        else:
            credits.append(t)

    rows = [_tx_to_flat(t, user_id, username, i, etl_at)
            for i, t in enumerate(credits)]
    rows += [_debit_to_flat(d, user_id, username, i, etl_at)
             for i, d in enumerate(debits)]

    # ---- users_flat projection ----
    growth = doc.get("growth") if isinstance(doc.get("growth"), dict) else {}
    segment_history = parse_segment_history(
        _pick(doc, "growth_history", "info.growth_history"))

    all_topups = [t for t in credits
                  if t.kind is TxKind.TOPUP and t.amount > 0]
    dated_topups = sorted((t for t in all_topups if t.dt), key=lambda t: t.dt)
    renewal_dts = sorted(d.dt for d in debits
                         if d.kind is DebitKind.RENEWAL and d.dt)
    # "second subscription" = a renewal ≥ 7 days after the first one, so
    # daily micro-billing doesn't count the next day's charge as retention
    second_sub_at = next(
        (dt for dt in renewal_dts
         if (dt - renewal_dts[0]).total_seconds() >= 7 * 86400), None
    ) if renewal_dts else None
    bypass = [d for d in debits if d.kind is DebitKind.BYPASS]
    device_debits = [d for d in debits if d.kind is DebitKind.DEVICE]
    promo_txs = [t for t in credits if t.kind is TxKind.PROMO]
    all_dts = [t.dt for t in credits if t.dt] + [d.dt for d in debits if d.dt]

    ref_stats_raw = _pick(doc, "ref_stats", "info.ref_stats") or {}
    if not isinstance(ref_stats_raw, dict):
        ref_stats_raw = {}

    def _count(value: Any) -> int:
        # referrals may be stored as a count OR as an array of telegram ids
        if isinstance(value, (list, tuple)):
            return len(value)
        return int(parse_amount(value) or 0)

    ref_stats = {
        "turnover_total": parse_amount(ref_stats_raw.get("turnover_total")) or 0.0,
        "earned_total": parse_amount(
            ref_stats_raw.get("earned_total") or ref_stats_raw.get("earned")) or 0.0,
        "referrals": _count(ref_stats_raw.get("referrals")),
        "paying_referrals": _count(ref_stats_raw.get("paying_referrals")),
        "payout_pending": parse_amount(
            ref_stats_raw.get("payout_pending")
            or ref_stats_raw.get("withdrawable")
            or ref_stats_raw.get("to_payout") or ref_stats_raw.get("balance")) or 0.0,
        "payout_history": ref_stats_raw.get("payout_history")
            if isinstance(ref_stats_raw.get("payout_history"), list) else [],
    }

    campaigns = _pick(doc, "campaigns", "info.campaigns")
    if not campaigns:
        # UTM attribution as a minimal campaign record
        utm = _pick(doc, "user_data.utm")
        if utm:
            campaigns = {"converted_from": str(utm)}

    referrer_id = _pick(doc, "referrer_id", "info.referrer_id",
                        "user_data.referrer")
    if referrer_id in ("", 0):  # the bot stores "" when there is no referrer
        referrer_id = None

    # registration attribution: paid/utm campaign > referral > organic
    reg_source = "organic"
    campaign_name = None
    if isinstance(campaigns, dict):
        campaign_name = campaigns.get("converted_from")
    elif isinstance(campaigns, list):
        campaign_name = next(
            (c.get("converted_from") for c in campaigns
             if isinstance(c, dict) and c.get("converted_from")), None)
    if campaign_name:
        name = str(campaign_name)
        # utm вида "ref_XXX" — это реферальные диплинки, не рекламный канал
        reg_source = ("referral" if name.lower().startswith(("ref_", "ref-"))
                      else name)
    elif referrer_id is not None:
        reg_source = "referral"
    devices = _parse_extra_devices(_pick(doc, "extraDevices", "info.extraDevices",
                                         "vpn.extraDevices", "vpn.extra_devices",
                                         "vpn.devices"))

    days_to_expire = parse_amount(_pick(doc, "days_to_expire",
                                        "vpn.days_to_expire"))
    if days_to_expire is None and isinstance(growth, dict):
        days_to_expire = parse_amount(growth.get("days_to_expire"))

    joined_at = parse_dt(_pick(doc, "joined_at", "created_at",
                               "growth.joined_at", "user_data.date_joined",
                               "info.joined_at", "user_data.joined_at",
                               "info.reg_date", "reg_date", "info.created_at"))

    def _rev_within(days: int) -> Optional[float]:
        """NET top-up revenue in the first N days after registration."""
        if joined_at is None:
            return None
        horizon = joined_at + timedelta(days=days)
        return round(sum(t.amount - t.bonus for t in dated_topups
                         if t.dt <= horizon), 2)

    user_row = {
        "_id": user_id,
        "username": username,
        "username_lower": username.lower() if username else None,
        "joined_at": joined_at,
        # NET top-up revenue in the first 7/30/90 days after registration —
        # the basis for "value of one registration" economics
        "rev_d7": _rev_within(7),
        "rev_d30": _rev_within(30),
        "rev_d90": _rev_within(90),
        "segment": growth.get("segment"),
        "segment_history": segment_history,
        # experiment groups maintained by the bot
        "ab_group": growth.get("ab_group"),
        "trial_ab_group": growth.get("trial_ab_group"),
        "days_to_expire": days_to_expire,
        "sub_until": parse_dt(_pick(doc, "sub_until", "info.sub_until",
                                    "subscription_until", "vpn.expireAt",
                                    "vpn.sub_until", "vpn.expires_at",
                                    "vpn.until", "growth.expire_at")),
        "balance": parse_amount(_pick(doc, "balance", "info.balance")) or 0.0,
        "referrer_id": referrer_id,
        "reg_source": reg_source,
        "ref_stats": ref_stats,
        "campaigns": campaigns if isinstance(campaigns, (dict, list)) else None,
        "extra_devices": devices,
        "extra_devices_active": sum(1 for d in devices if d["active"]),
        "preferred_client": _pick(doc, "preferred_client",
                                  "info.preferred_client",
                                  "vpn.preferred_client"),
        # funnel / cohort timestamps derived from normalized history
        "first_topup_at": dated_topups[0].dt if dated_topups else None,
        # totals over ALL topups — broken legacy rows without dates still count
        "topup_total": round(sum(t.amount for t in all_topups), 2),
        "topup_count": len(all_topups),
        "bonus_total": round(sum(t.bonus for t in credits), 2),
        "first_sub_at": renewal_dts[0] if renewal_dts else None,
        "second_sub_at": second_sub_at,
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


_MAX_LOG_DTS = _MAX_LOG_DTS_PROJ


def extract_activity_dts(doc: dict) -> list[datetime]:
    """Pull timestamps out of the free-form ``logs`` field for the
    hour × weekday activity heatmap."""
    logs = doc.get("logs")
    if isinstance(logs, dict):
        logs = list(logs.values())
    if not isinstance(logs, (list, tuple)):
        return []
    out: list[datetime] = []
    for entry in logs[-_MAX_LOG_DTS:]:
        dt = None
        if isinstance(entry, dict):
            dt = parse_dt(entry.get("dt") or entry.get("date") or entry.get("ts")
                          or entry.get("time") or entry.get("at"))
        elif isinstance(entry, (list, tuple)):
            for el in entry:
                dt = parse_dt(el)
                if dt:
                    break
        else:
            dt = parse_dt(entry)
        if dt:
            out.append(dt)
    return out


async def sweep_payments(db: AsyncIOMotorDatabase, etl_at: datetime,
                         full: bool = False) -> int:
    """Mirror ``payments_webhook`` into ``payments_flat`` (idempotent).

    Incremental by default: only documents newer than the last mirrored
    ``dt`` (minus a safety lag) are re-read; ``full=True`` sweeps everything.
    Returns the number of upserted rows; 0 when the collection is absent.
    """
    settings = get_settings()
    if settings.payments_db and getattr(db, "client", None) is not None:
        src_db = db.client[settings.payments_db]
    else:
        src_db = db
    # "payments" is the source of truth per the bot's schema; webhook-log
    # names are kept for older deployments
    candidates = (["payments", settings.payments_collection,
                   "payments_webhooks"]
                  if settings.payments_collection == "payments_webhook"
                  else [settings.payments_collection])
    existing = set(await src_db.list_collection_names())
    name = next((n for n in candidates if n and n in existing), None)
    if name is None:
        return 0

    query: dict = {}
    if not full:
        newest = await db[PAYMENTS_FLAT].find_one(
            {"dt": {"$type": "date"}}, sort=[("dt", -1)], projection={"dt": 1})
        if newest and newest.get("dt"):
            lag = newest["dt"] - timedelta(hours=6)
            query = {"$or": [{"created_at": {"$gte": lag}},
                             {"created_at": {"$exists": False}}]}

    ops: list = []
    count = 0
    async for doc in src_db[name].find(query).batch_size(500):
        payment = normalize_payment_webhook(doc)
        if payment is None:
            continue
        row = payment.model_dump()
        row["_id"] = row.pop("txid")
        row["etl_at"] = etl_at
        ops.append(ReplaceOne({"_id": row["_id"]}, row, upsert=True))
        count += 1
        if len(ops) >= _BULK_FLUSH:
            await db[PAYMENTS_FLAT].bulk_write(ops, ordered=False)
            ops = []
    if ops:
        await db[PAYMENTS_FLAT].bulk_write(ops, ordered=False)
    return count


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
    stats = {"users": 0, "tx_rows": 0, "unparsed": 0, "duplicate_ids": 0}
    # (weekday 0=Mon, hour) → count, for the activity heatmap
    heatmap: dict[tuple[int, int], int] = {}
    seen_user_ids: set[int] = set()

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
            stats["flatten_failures"] = stats.get("flatten_failures", 0) + 1
            log.exception("flatten_user failed", extra={"raw_id": str(doc.get("_id"))})
            continue
        if user_row is None:
            continue
        if user_row["_id"] in seen_user_ids:
            # two raw documents resolving to one telegram id would fight over
            # the same flat rows — keep the first, surface the anomaly
            stats["duplicate_ids"] += 1
            log.warning("duplicate user id in raw collection",
                        extra={"user_id": user_row["_id"],
                               "raw_id": str(doc.get("_id"))})
            continue
        seen_user_ids.add(user_row["_id"])

        stats["users"] += 1
        stats["tx_rows"] += len(rows)
        stats["unparsed"] += unparsed

        for row in rows:
            tx_ops.append(ReplaceOne({"_id": row["_id"]}, row, upsert=True))
        # rows that no longer exist in the source (shrunk lists, deleted
        # users) are purged after the sweep via the etl_at stamp
        user_ops.append(ReplaceOne({"_id": user_row["_id"]}, user_row, upsert=True))

        for dt in extract_activity_dts(doc):
            key = (dt.weekday(), dt.hour)
            heatmap[key] = heatmap.get(key, 0) + 1

        if len(tx_ops) >= _BULK_FLUSH:
            await flush()

    await flush()

    # purge rows of users deleted from the raw collection: every surviving
    # row was just re-stamped with this run's etl_at (payments_flat is
    # incremental, so only user-derived collections are purged). Skipped when
    # any document failed to flatten — a code bug must not cascade into
    # deleting that user's history.
    if not stats.get("flatten_failures"):
        await db[TX_FLAT].delete_many({"etl_at": {"$lt": etl_at}})
        await db[USERS_FLAT].delete_many({"etl_at": {"$lt": etl_at}})

    try:
        stats["payments"] = await sweep_payments(db, etl_at)
    except Exception:
        log.exception("payments_webhook sweep failed")
        stats["payments"] = 0

    await db[ACTIVITY].replace_one(
        {"_id": "heatmap"},
        {"_id": "heatmap",
         "cells": [{"dow": k[0], "hour": k[1], "count": v}
                   for k, v in sorted(heatmap.items())],
         "computed_at": etl_at},
        upsert=True)

    stats["took_ms"] = int((time.monotonic() - started) * 1000)
    stats["ran_at"] = etl_at
    await db[ETL_STATE].replace_one(
        {"_id": "etl"}, {"_id": "etl", **stats}, upsert=True)
    log.info("etl completed", extra={k: v for k, v in stats.items()
                                     if k != "ran_at"})
    return stats
