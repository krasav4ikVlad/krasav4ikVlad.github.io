"""Overview router: live summary counters, payment-provider health, event feed.

``GET /overview/summary`` is a live endpoint (never cached): it combines the
Remnawave online counter with Mongo aggregates over the flat collections.
``GET /overview/providers-status`` is cached for 60 seconds and computes
median payment gaps per top-up source in Python from the last 50 payment
timestamps. ``GET /overview/events/recent`` drains the in-process WebSocket
ring buffer (``app.ws.hub``).
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

from ..agg import NET_AMOUNT, as_utc, bucket_expr, iso, r2
from ..cache import cached
from ..config import get_settings
from ..db import TX_FLAT, USERS_FLAT, get_db
from ..remnawave import get_remnawave
from ..ws import hub

log = logging.getLogger("app.overview")
router = APIRouter(prefix="/overview", tags=["overview"])

_SPARKLINE_DAYS = 30
_PROVIDER_WINDOW_DAYS = 30
_PROVIDER_GAP_SAMPLE = 50  # payments per source used for the median gap

# Net top-up revenue rows (real money received).
_TOPUP_MATCH: dict[str, Any] = {"direction": "credit", "kind": "topup"}


def _day_key(dt: Optional[datetime]) -> Optional[str]:
    dt = as_utc(dt)
    return dt.date().isoformat() if dt else None


def _zero_filled(values: dict[str, float], last_day: date, days: int,
                 as_int: bool = False) -> list[dict[str, Any]]:
    """Daily sparkline points for the ``days`` days ending at ``last_day``."""
    points: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        key = (last_day - timedelta(days=offset)).isoformat()
        value = values.get(key, 0)
        points.append({"date": key,
                       "value": int(value) if as_int else r2(value)})
    return points


# ---------------------------------------------------------------------------
# GET /overview/summary — live counters, NOT cached
# ---------------------------------------------------------------------------

@router.get("/summary")
async def summary() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spark_start = today_start - timedelta(days=_SPARKLINE_DAYS - 1)

    # --- Remnawave online counter: a panel failure must never 500 us ---
    online_users: Optional[int] = None
    try:
        online_users = await get_remnawave().get_online_count()
    except Exception:
        log.warning("remnawave online count failed", exc_info=True)
    remnawave_available = online_users is not None

    # --- net top-up revenue counters (trailing windows from now) ---
    revenue_today = revenue_7d = revenue_30d = 0.0
    counter_rows = await db[TX_FLAT].aggregate([
        {"$match": {**_TOPUP_MATCH,
                    "dt": {"$type": "date", "$gte": now - timedelta(days=30)}}},
        {"$group": {
            "_id": None,
            "today": {"$sum": {"$cond": [
                {"$gte": ["$dt", today_start]}, NET_AMOUNT, 0]}},
            "d7": {"$sum": {"$cond": [
                {"$gte": ["$dt", now - timedelta(days=7)]}, NET_AMOUNT, 0]}},
            "d30": {"$sum": NET_AMOUNT},
        }},
    ]).to_list(1)
    if counter_rows:
        row = counter_rows[0]
        revenue_today = r2(row.get("today"))
        revenue_7d = r2(row.get("d7"))
        revenue_30d = r2(row.get("d30"))

    # --- daily net revenue sparkline (last 30 calendar days, UTC) ---
    revenue_by_day: dict[str, float] = {}
    for row in await db[TX_FLAT].aggregate([
        {"$match": {**_TOPUP_MATCH,
                    "dt": {"$type": "date", "$gte": spark_start}}},
        {"$group": {"_id": bucket_expr("day"), "value": {"$sum": NET_AMOUNT}}},
    ]).to_list(None):
        key = _day_key(row.get("_id"))
        if key is not None:
            revenue_by_day[key] = float(row.get("value") or 0.0)

    # --- daily registrations sparkline + today's counter ---
    regs_by_day: dict[str, float] = {}
    for row in await db[USERS_FLAT].aggregate([
        {"$match": {"joined_at": {"$type": "date", "$gte": spark_start}}},
        {"$group": {"_id": bucket_expr("day", "$joined_at"),
                    "value": {"$sum": 1}}},
    ]).to_list(None):
        key = _day_key(row.get("_id"))
        if key is not None:
            regs_by_day[key] = float(row.get("value") or 0)
    registrations_today = int(regs_by_day.get(today_start.date().isoformat(), 0))

    # --- active subscriptions ---
    active_subs = await db[USERS_FLAT].count_documents({
        "$or": [{"segment": {"$regex": "^active"}},
                {"days_to_expire": {"$gt": 0}}],
    })

    today = today_start.date()
    return {
        "online_users": online_users,
        "remnawave_available": remnawave_available,
        "active_subs": int(active_subs),
        "revenue_today": revenue_today,
        "revenue_7d": revenue_7d,
        "revenue_30d": revenue_30d,
        "registrations_today": registrations_today,
        "sparklines": {
            "revenue": _zero_filled(revenue_by_day, today, _SPARKLINE_DAYS),
            "registrations": _zero_filled(regs_by_day, today, _SPARKLINE_DAYS,
                                          as_int=True),
            "online": [],       # no history source yet
            "active_subs": [],  # no history source yet
        },
    }


# ---------------------------------------------------------------------------
# GET /overview/providers-status — cached 60s
# ---------------------------------------------------------------------------

@cached(ttl=60, prefix="overview:providers-status")
async def _providers_status() -> dict[str, Any]:
    db = get_db()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=_PROVIDER_WINDOW_DAYS)

    rows = await db[TX_FLAT].aggregate([
        {"$match": {**_TOPUP_MATCH,
                    "source": {"$type": "string"},
                    "dt": {"$type": "date", "$gte": since}}},
        {"$sort": {"dt": -1}},
        {"$group": {
            "_id": "$source",
            "last_payment_at": {"$first": "$dt"},
            "payments_24h": {"$sum": {"$cond": [
                {"$gte": ["$dt", now - timedelta(hours=24)]}, 1, 0]}},
            "payments_7d": {"$sum": {"$cond": [
                {"$gte": ["$dt", now - timedelta(days=7)]}, 1, 0]}},
            "dts": {"$push": "$dt"},
        }},
        {"$project": {"last_payment_at": 1, "payments_24h": 1,
                      "payments_7d": 1,
                      "dts": {"$slice": ["$dts", _PROVIDER_GAP_SAMPLE]}}},
        {"$sort": {"_id": 1}},
    ]).to_list(None)

    providers: list[dict[str, Any]] = []
    for row in rows:
        # dts are sorted desc, so consecutive gaps are non-negative
        dts = [as_utc(d) for d in row.get("dts") or []
               if isinstance(d, datetime)]
        gaps = [(dts[i] - dts[i + 1]).total_seconds() / 3600
                for i in range(len(dts) - 1)]
        median_gap = float(statistics.median(gaps)) if gaps else 0.0

        last_at = as_utc(row.get("last_payment_at"))
        if last_at is not None:
            silence = max((now - last_at).total_seconds() / 3600, 0.0)
        else:  # defensive: cannot happen with the $match above
            silence = float(_PROVIDER_WINDOW_DAYS * 24)

        threshold = max(float(settings.provider_silence_hours),
                        4 * median_gap)
        if silence > threshold:
            status = "down"
        elif silence > threshold / 2:
            status = "warning"
        else:
            status = "ok"

        providers.append({
            "source": str(row.get("_id")),
            "last_payment_at": iso(last_at),
            "payments_24h": int(row.get("payments_24h") or 0),
            "payments_7d": int(row.get("payments_7d") or 0),
            "median_gap_hours": r2(median_gap),
            "silence_hours": r2(silence),
            "threshold_hours": r2(threshold),
            "status": status,
        })
    return {"providers": providers}


@router.get("/providers-status")
async def providers_status() -> dict[str, Any]:
    return await _providers_status()


# ---------------------------------------------------------------------------
# GET /overview/events/recent — live ring buffer, NOT cached
# ---------------------------------------------------------------------------

@router.get("/events/recent")
async def recent_events(
    limit: int = Query(50, ge=1, le=100, description="Max events to return"),
) -> dict[str, Any]:
    return {"events": hub.recent(limit)}
