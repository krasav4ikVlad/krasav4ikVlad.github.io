"""Referral program analytics: top referrers, program summary, payouts, income
timeseries.

Everything is served from ``users_flat.ref_stats`` except the income timeseries,
which aggregates ``kind=ref_income`` credits in ``transactions_flat``. Payout
history entries are raw legacy data (dicts or even bare lists) and are always
masked with :func:`app.masking.mask_payout_entry` before leaving the API.
Cached helpers accept only JSON-serializable kwargs per the caching contract.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query

from ..agg import as_utc, bucket_expr, iso, r2
from ..cache import cached
from ..db import TX_FLAT, USERS_FLAT, get_db
from ..masking import mask_payout_entry
from ..normalizer import parse_dt
from .deps import Granularity, Period, get_period

router = APIRouter(prefix="/referrals", tags=["referrals"])

_QUEUE_MIN_PENDING = 100.0
_QUEUE_LIMIT = 50
_HISTORY_LIMIT = 50

_SORT_FIELDS = {
    "turnover": "ref_stats.turnover_total",
    "earned": "ref_stats.earned_total",
    "paying": "ref_stats.paying_referrals",
}

# keys commonly holding a payout timestamp in raw history entries
_DT_KEYS = ("dt", "date", "ts", "time", "at", "created_at", "createdAt",
            "paid_at", "processed_at", "timestamp", "when")

# a bare number-ish string is almost always an amount / card, not a date
_NUMERIC_RE = re.compile(r"^[\d\s.,+-]*$")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def _ref_stats(doc: dict) -> dict:
    ref = doc.get("ref_stats")
    return ref if isinstance(ref, dict) else {}


def _entry_dt(entry: Any) -> Optional[datetime]:
    """Best-effort UTC timestamp of one raw payout-history entry.

    Entries may be dicts, bare lists or scalars. Well-known dt keys are tried
    first; then any date-looking value. Pure-numeric strings/numbers outside
    the known keys are skipped — those are amounts, not epochs.
    """
    candidates: list[Any]
    if isinstance(entry, dict):
        for key in _DT_KEYS:
            dt = parse_dt(entry.get(key))
            if dt is not None:
                return as_utc(dt)
        candidates = list(entry.values())
    elif isinstance(entry, (list, tuple)):
        candidates = list(entry)
    else:
        candidates = [entry]

    for value in candidates:
        date_like = (
            isinstance(value, datetime)
            or (isinstance(value, dict) and "$date" in value)
            or (isinstance(value, str) and value.strip()
                and not _NUMERIC_RE.match(value.strip()))
        )
        if not date_like:
            continue
        dt = parse_dt(value)
        if dt is not None:
            return as_utc(dt)
    return None


# ---------------------------------------------------------------------------
# GET /referrals/top
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="referrals:top")
async def _top(*, by: str, limit: int) -> dict[str, Any]:
    db = get_db()
    sort_field = _SORT_FIELDS.get(by, _SORT_FIELDS["turnover"])
    cursor = db[USERS_FLAT].find(
        {"ref_stats.referrals": {"$gt": 0}},
        {"username": 1, "ref_stats.turnover_total": 1, "ref_stats.earned_total": 1,
         "ref_stats.referrals": 1, "ref_stats.paying_referrals": 1,
         "ref_stats.payout_pending": 1},
    ).sort(sort_field, -1).limit(limit)

    referrers: list[dict[str, Any]] = []
    async for doc in cursor:
        ref = _ref_stats(doc)
        referrals = int(_num(ref.get("referrals")))
        paying = int(_num(ref.get("paying_referrals")))
        referrers.append({
            "user_id": doc.get("_id"),
            "username": doc.get("username"),
            "turnover_total": r2(ref.get("turnover_total")),
            "earned_total": r2(ref.get("earned_total")),
            "referrals": referrals,
            "paying_referrals": paying,
            "conversion_pct": r2(paying / referrals * 100) if referrals else 0.0,
            "payout_pending": r2(ref.get("payout_pending")),
        })
    return {"referrers": referrers}


@router.get("/top")
async def top(by: Literal["turnover", "earned", "paying"] = Query("turnover"),
              limit: int = Query(20, ge=1, le=100)) -> dict:
    """Top referrers by turnover / earnings / paying referrals."""
    return await _top(by=by, limit=limit)


# ---------------------------------------------------------------------------
# GET /referrals/summary
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="referrals:summary")
async def _summary() -> dict[str, Any]:
    db = get_db()
    has_referrals = {"$gt": [{"$ifNull": ["$ref_stats.referrals", 0]}, 0]}
    rows = await db[USERS_FLAT].aggregate([
        {"$group": {
            "_id": None,
            "total_referrers": {"$sum": {"$cond": [has_referrals, 1, 0]}},
            "total_referrals": {"$sum": {"$ifNull": ["$ref_stats.referrals", 0]}},
            "total_paying": {"$sum": {"$ifNull": ["$ref_stats.paying_referrals", 0]}},
            "payout_pending_total": {
                "$sum": {"$ifNull": ["$ref_stats.payout_pending", 0]}},
            "earned_total": {"$sum": {"$ifNull": ["$ref_stats.earned_total", 0]}},
            "turnover_total": {"$sum": {"$ifNull": ["$ref_stats.turnover_total", 0]}},
        }},
    ]).to_list(length=1)
    stats = rows[0] if rows else {}

    total_referrals = int(_num(stats.get("total_referrals")))
    total_paying = int(_num(stats.get("total_paying")))
    return {
        "total_referrers": int(_num(stats.get("total_referrers"))),
        "total_referrals": total_referrals,
        "total_paying": total_paying,
        "conversion_pct": (r2(total_paying / total_referrals * 100)
                           if total_referrals else 0.0),
        "payout_pending_total": r2(stats.get("payout_pending_total")),
        "earned_total": r2(stats.get("earned_total")),
        "turnover_total": r2(stats.get("turnover_total")),
    }


@router.get("/summary")
async def summary() -> dict:
    """Program-wide referral totals from ``users_flat.ref_stats``."""
    return await _summary()


# ---------------------------------------------------------------------------
# GET /referrals/payouts
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="referrals:payouts")
async def _payouts() -> dict[str, Any]:
    db = get_db()
    uf = db[USERS_FLAT]

    rows = await uf.aggregate([
        {"$group": {"_id": None,
                    "pending": {"$sum": {"$ifNull": ["$ref_stats.payout_pending",
                                                     0]}}}},
    ]).to_list(length=1)
    pending_total = r2(rows[0].get("pending")) if rows else 0.0

    queue: list[dict[str, Any]] = []
    cursor = uf.find(
        {"ref_stats.payout_pending": {"$gte": _QUEUE_MIN_PENDING}},
        {"username": 1, "ref_stats.payout_pending": 1},
    ).sort("ref_stats.payout_pending", -1).limit(_QUEUE_LIMIT)
    async for doc in cursor:
        queue.append({
            "user_id": doc.get("_id"),
            "username": doc.get("username"),
            "payout_pending": r2(_ref_stats(doc).get("payout_pending")),
        })

    # flatten raw payout_history lists (entries may be dicts or bare lists)
    dated: list[tuple[datetime, dict]] = []
    cursor = uf.find(
        {"ref_stats.payout_history.0": {"$exists": True}},
        {"username": 1, "ref_stats.payout_history": 1},
    )
    async for doc in cursor:
        history = _ref_stats(doc).get("payout_history")
        if not isinstance(history, list):
            continue
        for raw in history:
            dt = _entry_dt(raw)
            masked = mask_payout_entry(raw)
            if isinstance(masked, dict):
                extra = {k: v for k, v in masked.items()
                         if k not in ("user_id", "username")}
            else:
                extra = {"entry": masked}
            row: dict[str, Any] = {"user_id": doc.get("_id"),
                                   "username": doc.get("username"), **extra}
            if dt is not None:
                row["dt"] = iso(dt)
            dated.append((dt or _EPOCH, row))

    dated.sort(key=lambda item: item[0], reverse=True)
    history_rows = [row for _, row in dated[:_HISTORY_LIMIT]]

    return {"pending_total": pending_total, "queue": queue,
            "history": history_rows}


@router.get("/payouts")
async def payouts() -> dict:
    """Pending payout queue plus masked flattened payout history."""
    return await _payouts()


# ---------------------------------------------------------------------------
# GET /referrals/timeseries
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="referrals:timeseries")
async def _timeseries(*, granularity: str, from_iso: Optional[str],
                      to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "credit", "kind": "ref_income",
                    **_dt_match(period)}},
        {"$group": {"_id": bucket_expr(granularity),
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]).to_list(length=None)

    series: list[dict[str, Any]] = []
    for row in rows:
        bucket = as_utc(row.get("_id"))
        if not isinstance(bucket, datetime):
            continue
        series.append({"bucket": iso(bucket), "amount": r2(row.get("amount")),
                       "count": int(row.get("count") or 0)})
    return {"series": series}


@router.get("/timeseries")
async def timeseries(granularity: Granularity = Query("day"),
                     period: Period = Depends(get_period)) -> dict:
    """Referral income (``kind=ref_income`` credits) over time."""
    return await _timeseries(granularity=granularity,
                             from_iso=iso(period.from_dt),
                             to_iso=iso(period.to_dt))
