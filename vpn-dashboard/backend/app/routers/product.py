"""Product analytics: extra device slots, bypass purchases, preferred clients.

All heavy aggregations run in MongoDB against ``transactions_flat`` /
``users_flat``; only light final assembly (gap averaging, zero-filling)
happens in Python.  Cached helpers accept only JSON-serializable kwargs per
the caching contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..agg import as_utc, bucket_expr, iso, r2
from ..cache import cached
from ..db import TX_FLAT, USERS_FLAT, get_db
from .deps import Granularity, Period, get_period

router = APIRouter(prefix="/product", tags=["product"])

_TOP_CONSUMERS = 15
_TS_MONTHS = 12


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _period_from_iso(from_iso: Optional[str], to_iso: Optional[str]) -> Period:
    """Rebuild a ``Period`` inside cached helpers (kwargs must be JSON-safe)."""
    from_dt = datetime.fromisoformat(from_iso) if from_iso else None
    to_dt = (datetime.fromisoformat(to_iso) if to_iso
             else datetime.now(timezone.utc))
    return Period(as_utc(from_dt), as_utc(to_dt) or datetime.now(timezone.utc))


def _dt_match(period: Period) -> dict:
    """Period match on ``dt`` that also excludes null/broken dt rows."""
    cond: dict[str, Any] = dict(period.match()["dt"])
    cond["$type"] = "date"
    return {"dt": cond}


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


# ---------------------------------------------------------------------------
# GET /product/devices
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="product:devices")
async def _devices() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)

    # slot counters from users_flat: active from the precomputed counter,
    # churned by folding extra_devices entries with active: false
    slot_rows = await db[USERS_FLAT].aggregate([
        {"$project": {
            "active": {"$ifNull": ["$extra_devices_active", 0]},
            "churned": {"$size": {"$filter": {
                "input": {"$cond": [{"$isArray": "$extra_devices"},
                                    "$extra_devices", []]},
                "as": "d",
                "cond": {"$eq": ["$$d.active", False]},
            }}},
        }},
        {"$group": {
            "_id": None,
            "active_slots": {"$sum": "$active"},
            "users_with_devices": {
                "$sum": {"$cond": [{"$gt": ["$active", 0]}, 1, 0]}},
            "churned_slots": {"$sum": "$churned"},
        }},
    ]).to_list(length=1)
    slots = slot_rows[0] if slot_rows else {}

    async def _device_sum(start: Optional[datetime]) -> float:
        match: dict[str, Any] = {"direction": "debit", "kind": "device"}
        if start is not None:
            match["dt"] = {"$type": "date", "$gte": start, "$lt": now}
        rows = await db[TX_FLAT].aggregate([
            {"$match": match},
            {"$group": {"_id": None,
                        "amount": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]).to_list(length=1)
        return r2(rows[0].get("amount")) if rows else 0.0

    device_mrr = await _device_sum(now - timedelta(days=30))
    revenue_total = await _device_sum(None)

    # last 12 calendar months (current month included), zero-filled
    months: list[datetime] = []
    for i in range(_TS_MONTHS - 1, -1, -1):
        offset = now.month - 1 - i
        months.append(datetime(now.year + offset // 12, offset % 12 + 1, 1,
                               tzinfo=timezone.utc))
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "debit", "kind": "device",
                    "dt": {"$type": "date", "$gte": months[0], "$lt": now}}},
        {"$group": {"_id": bucket_expr("month"),
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "count": {"$sum": 1}}},
    ]).to_list(length=None)
    by_month: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = as_utc(row.get("_id"))
        if not isinstance(bucket, datetime):
            continue
        by_month[f"{bucket.year:04d}-{bucket.month:02d}"] = {
            "amount": r2(row.get("amount")),
            "count": int(row.get("count") or 0),
        }
    timeseries = [
        {"bucket": iso(month),
         **by_month.get(f"{month.year:04d}-{month.month:02d}",
                        {"amount": 0.0, "count": 0})}
        for month in months
    ]

    return {
        "active_slots": int(_num(slots.get("active_slots"))),
        "users_with_devices": int(_num(slots.get("users_with_devices"))),
        "device_mrr": device_mrr,
        "churned_slots": int(_num(slots.get("churned_slots"))),
        "revenue_total": revenue_total,
        "timeseries": timeseries,
    }


@router.get("/devices")
async def devices() -> dict:
    """Extra device slots, trailing-30d device MRR and monthly device revenue."""
    return await _devices()


# ---------------------------------------------------------------------------
# GET /product/bypass
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="product:bypass")
async def _bypass(*, granularity: str, from_iso: Optional[str],
                  to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    match = {"direction": "debit", "kind": "bypass", **_dt_match(period)}
    tx = db[TX_FLAT]

    facets = await tx.aggregate([
        {"$match": match},
        {"$group": {"_id": "$user_id",
                    "username": {"$last": "$username"},
                    "purchases": {"$sum": 1},
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        {"$facet": {
            "summary": [{"$group": {
                "_id": None,
                "purchases": {"$sum": "$purchases"},
                "revenue": {"$sum": "$amount"},
                "unique_buyers": {"$sum": 1},
                "repeat_buyers": {
                    "$sum": {"$cond": [{"$gte": ["$purchases", 2]}, 1, 0]}},
            }}],
            "top": [{"$sort": {"purchases": -1, "amount": -1}},
                    {"$limit": _TOP_CONSUMERS}],
        }},
    ]).to_list(length=1)
    facet = facets[0] if facets else {}
    summary_rows = facet.get("summary") or []
    summary = summary_rows[0] if summary_rows else {}

    top_consumers = [
        {"user_id": row.get("_id"), "username": row.get("username"),
         "purchases": int(row.get("purchases") or 0),
         "amount": r2(row.get("amount"))}
        for row in facet.get("top") or []
    ]

    # mean gap between repeat purchases: per-user sorted dts, consecutive
    # diffs in days, averaged over all gaps (users with >= 2 purchases only)
    gap_rows = await tx.aggregate([
        {"$match": match},
        {"$group": {"_id": "$user_id", "dts": {"$push": "$dt"},
                    "purchases": {"$sum": 1}}},
        {"$match": {"purchases": {"$gte": 2}}},
        {"$project": {"_id": 0, "dts": 1}},
    ]).to_list(length=None)
    gaps: list[float] = []
    for row in gap_rows:
        dts = sorted(d for d in (as_utc(x) for x in row.get("dts") or [])
                     if isinstance(d, datetime))
        gaps.extend((b - a).total_seconds() / 86400
                    for a, b in zip(dts, dts[1:]))
    avg_days_between = r2(sum(gaps) / len(gaps)) if gaps else None

    ts_rows = await tx.aggregate([
        {"$match": match},
        {"$group": {"_id": bucket_expr(granularity),
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]).to_list(length=None)
    timeseries: list[dict[str, Any]] = []
    for row in ts_rows:
        bucket = as_utc(row.get("_id"))
        if not isinstance(bucket, datetime):
            continue
        timeseries.append({"bucket": iso(bucket),
                           "amount": r2(row.get("amount")),
                           "count": int(row.get("count") or 0)})

    unique_buyers = int(summary.get("unique_buyers") or 0)
    repeat_buyers = int(summary.get("repeat_buyers") or 0)
    return {
        "purchases": int(summary.get("purchases") or 0),
        "revenue": r2(summary.get("revenue")),
        "unique_buyers": unique_buyers,
        "repeat_buyers": repeat_buyers,
        "repeat_rate_pct": (r2(repeat_buyers / unique_buyers * 100)
                            if unique_buyers else 0.0),
        "avg_days_between": avg_days_between,
        "top_consumers": top_consumers,
        "timeseries": timeseries,
    }


@router.get("/bypass")
async def bypass(granularity: Granularity = Query("day"),
                 period: Period = Depends(get_period)) -> dict:
    """Bypass purchases: volume, repeat buying behaviour and top consumers."""
    return await _bypass(granularity=granularity,
                         from_iso=iso(period.from_dt),
                         to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /product/clients
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="product:clients")
async def _clients() -> dict[str, Any]:
    db = get_db()
    rows = await db[USERS_FLAT].aggregate([
        {"$group": {"_id": {"$ifNull": ["$preferred_client", "unknown"]},
                    "count": {"$sum": 1}}},
    ]).to_list(length=None)

    folded: dict[str, int] = {}
    for row in rows:
        raw = row.get("_id")
        client = str(raw) if raw not in (None, "") else "unknown"
        folded[client] = folded.get(client, 0) + int(row.get("count") or 0)

    clients = [{"client": client, "count": count}
               for client, count in sorted(folded.items(),
                                           key=lambda kv: (-kv[1], kv[0]))]
    return {"clients": clients}


@router.get("/clients")
async def clients() -> dict:
    """Preferred VPN client distribution across users."""
    return await _clients()
