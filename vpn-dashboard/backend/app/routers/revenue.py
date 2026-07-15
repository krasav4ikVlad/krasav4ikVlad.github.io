"""Revenue analytics: timeseries, KPIs, LTV cohorts, spend types, bonus share,
month/week comparisons.

All heavy aggregations run in MongoDB against ``transactions_flat``; only light
final assembly (medians, cohort folding, zero-filling) happens in Python.
Cached helpers accept only JSON-serializable kwargs per the caching contract.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..agg import NET_AMOUNT, as_utc, bucket_expr, iso, r2
from ..cache import cached
from ..db import TX_FLAT, USERS_FLAT, get_db
from .deps import Granularity, Period, get_period

router = APIRouter(prefix="/revenue", tags=["revenue"])

_MEDIAN_CAP = 50_000

_TYPE_ORDER = ["renewal", "device", "bypass", "gift", "other"]
_TYPE_LABELS = {
    "renewal": "Подписки",
    "device": "Устройства",
    "bypass": "Обход блокировок",
    "gift": "Подарки",
    "other": "Прочее",
}


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


def _topup_match(period: Period) -> dict:
    return {"direction": "credit", "kind": "topup", **_dt_match(period)}


def _change_pct(current: float, previous: float) -> Optional[float]:
    if not previous:
        return None
    return r2((current - previous) / previous * 100)


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


async def _net_sum(db: Any, start: datetime, end: datetime) -> float:
    """NET topup revenue over ``[start, end)``."""
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "credit", "kind": "topup",
                    "dt": {"$type": "date", "$gte": start, "$lt": end}}},
        {"$group": {"_id": None, "revenue": {"$sum": NET_AMOUNT}}},
    ]).to_list(length=1)
    return r2(rows[0].get("revenue")) if rows else 0.0


# ---------------------------------------------------------------------------
# GET /revenue/timeseries
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="revenue:timeseries")
async def _timeseries(*, granularity: str, from_iso: Optional[str],
                      to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    rows = await db[TX_FLAT].aggregate([
        {"$match": _topup_match(period)},
        {"$group": {
            "_id": {"bucket": bucket_expr(granularity),
                    "source": {"$ifNull": ["$source", "other"]}},
            "revenue": {"$sum": NET_AMOUNT},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.bucket": 1, "_id.source": 1}},
    ]).to_list(length=None)

    series: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    for row in rows:
        key = row.get("_id") or {}
        bucket = as_utc(key.get("bucket"))
        if not isinstance(bucket, datetime):
            continue
        source = str(key.get("source") or "other")
        revenue = r2(row.get("revenue"))
        series.append({"bucket": iso(bucket), "source": source,
                       "revenue": revenue, "count": int(row.get("count") or 0)})
        totals[source] = totals.get(source, 0.0) + revenue
    sources = sorted(totals, key=lambda s: (-totals[s], s))
    return {"series": series, "sources": sources}


@router.get("/timeseries")
async def timeseries(granularity: Granularity = Query("day"),
                     period: Period = Depends(get_period)) -> dict:
    """Stacked-bar data: NET topup revenue grouped by (bucket, source)."""
    return await _timeseries(granularity=granularity,
                             from_iso=iso(period.from_dt),
                             to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /revenue/kpis
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="revenue:kpis")
async def _kpis(*, from_iso: Optional[str],
                to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    tx = db[TX_FLAT]

    # trailing 30 days from the period end, regardless of the period start
    trailing_start = period.to_dt - timedelta(days=30)

    async def _debit_sum(kind: str) -> float:
        rows = await tx.aggregate([
            {"$match": {"direction": "debit", "kind": kind,
                        "dt": {"$type": "date", "$gte": trailing_start,
                               "$lt": period.to_dt}}},
            {"$group": {"_id": None, "amount": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]).to_list(length=1)
        return r2(rows[0].get("amount")) if rows else 0.0

    mrr = await _debit_sum("renewal")
    device_mrr = await _debit_sum("device")

    match = _topup_match(period)
    rows = await tx.aggregate([
        {"$match": match},
        {"$group": {"_id": None,
                    "revenue": {"$sum": NET_AMOUNT},
                    "count": {"$sum": 1},
                    "users": {"$addToSet": "$user_id"}}},
        {"$project": {"revenue": 1, "count": 1,
                      "paying_users": {"$size": "$users"}}},
    ]).to_list(length=1)
    summary = rows[0] if rows else {}
    revenue = r2(summary.get("revenue"))
    payments_count = int(summary.get("count") or 0)
    paying_users = int(summary.get("paying_users") or 0)

    median_check = 0.0
    if payments_count:
        sample: list[dict] = ([{"$sample": {"size": _MEDIAN_CAP}}]
                              if payments_count > _MEDIAN_CAP else [])
        net_rows = await tx.aggregate([
            {"$match": match}, *sample,
            {"$project": {"_id": 0, "net": NET_AMOUNT}},
        ]).to_list(length=_MEDIAN_CAP)
        values = [_num(r.get("net")) for r in net_rows]
        if values:
            median_check = r2(statistics.median(values))

    prev = period.previous()
    prev_rows = await tx.aggregate([
        {"$match": _topup_match(prev)},
        {"$group": {"_id": None, "revenue": {"$sum": NET_AMOUNT}}},
    ]).to_list(length=1)
    prev_revenue = r2(prev_rows[0].get("revenue")) if prev_rows else 0.0

    return {
        "mrr": mrr,
        "device_mrr": device_mrr,
        "arpu": r2(revenue / paying_users) if paying_users else 0.0,
        "avg_check": r2(revenue / payments_count) if payments_count else 0.0,
        "median_check": median_check,
        "paying_users": paying_users,
        "payments_count": payments_count,
        "revenue": revenue,
        "prev_revenue": prev_revenue,
        "revenue_change_pct": _change_pct(revenue, prev_revenue),
    }


@router.get("/kpis")
async def kpis(period: Period = Depends(get_period)) -> dict:
    """MRR, ARPU, checks and net revenue vs the previous period."""
    return await _kpis(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /revenue/ltv-cohorts
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="revenue:ltv-cohorts")
async def _ltv_cohorts() -> dict[str, Any]:
    db = get_db()

    # user_id -> joined month, plus cohort sizes, from a light projection scan
    month_by_user: dict[Any, str] = {}
    cohorts: dict[str, dict[str, Any]] = {}
    cursor = db[USERS_FLAT].find({}, {"_id": 1, "joined_at": 1})
    async for doc in cursor:
        joined = as_utc(doc.get("joined_at"))
        if not isinstance(joined, datetime):
            continue
        month = f"{joined.year:04d}-{joined.month:02d}"
        month_by_user[doc.get("_id")] = month
        bucket = cohorts.setdefault(month, {"users": 0, "revenue": 0.0})
        bucket["users"] += 1

    # lifetime NET topups per user, folded into the joined-month cohorts
    rows = db[TX_FLAT].aggregate([
        {"$match": {"direction": "credit", "kind": "topup"}},
        {"$group": {"_id": "$user_id", "revenue": {"$sum": NET_AMOUNT}}},
    ])
    async for row in rows:
        month = month_by_user.get(row.get("_id"))
        if month is None:
            continue
        cohorts[month]["revenue"] += _num(row.get("revenue"))

    out = []
    for month in sorted(cohorts):
        users = cohorts[month]["users"]
        revenue = r2(cohorts[month]["revenue"])
        out.append({"cohort": month, "users": users, "revenue": revenue,
                    "ltv": r2(revenue / users) if users else 0.0})
    return {"cohorts": out}


@router.get("/ltv-cohorts")
async def ltv_cohorts() -> dict:
    """Lifetime NET topup revenue per joined-month cohort."""
    return await _ltv_cohorts()


# ---------------------------------------------------------------------------
# GET /revenue/by-type
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="revenue:by-type")
async def _by_type(*, from_iso: Optional[str],
                   to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "debit", **_dt_match(period)}},
        {"$group": {"_id": {"$ifNull": ["$kind", "other"]},
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "count": {"$sum": 1}}},
    ]).to_list(length=None)

    folded: dict[str, dict[str, Any]] = {
        k: {"amount": 0.0, "count": 0} for k in _TYPE_ORDER}
    for row in rows:
        kind = str(row.get("_id") or "other")
        if kind not in folded:
            kind = "other"
        folded[kind]["amount"] += _num(row.get("amount"))
        folded[kind]["count"] += int(row.get("count") or 0)

    return {"types": [
        {"kind": kind, "label": _TYPE_LABELS[kind],
         "amount": r2(folded[kind]["amount"]), "count": folded[kind]["count"]}
        for kind in _TYPE_ORDER
    ]}


@router.get("/by-type")
async def by_type(period: Period = Depends(get_period)) -> dict:
    """What the money was spent on, from debit rows."""
    return await _by_type(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /revenue/bonus-share
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="revenue:bonus-share")
async def _bonus_share(*, granularity: str, from_iso: Optional[str],
                       to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    is_topup = {"$eq": ["$kind", "topup"]}
    is_promo = {"$eq": ["$kind", "promo"]}
    rows = await db[TX_FLAT].aggregate([
        # kind=bonus are standalone gifted accruals ("Бонус за возвращение")
        {"$match": {"direction": "credit",
                    "kind": {"$in": ["topup", "promo", "bonus"]},
                    **_dt_match(period)}},
        {"$group": {
            "_id": bucket_expr(granularity),
            "net": {"$sum": {"$cond": [is_topup, NET_AMOUNT, 0]}},
            "bonus": {"$sum": {"$cond": [
                is_topup, {"$ifNull": ["$bonus", 0]},
                {"$cond": [is_promo, 0, {"$ifNull": ["$amount", 0]}]}]}},
            "promo": {"$sum": {"$cond": [is_promo,
                                         {"$ifNull": ["$amount", 0]}, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]).to_list(length=None)

    series: list[dict[str, Any]] = []
    net_total = bonus_total = promo_total = 0.0
    for row in rows:
        bucket = as_utc(row.get("_id"))
        if not isinstance(bucket, datetime):
            continue
        net, bonus, promo = (_num(row.get("net")), _num(row.get("bonus")),
                             _num(row.get("promo")))
        net_total += net
        bonus_total += bonus
        promo_total += promo
        series.append({"bucket": iso(bucket), "net": r2(net),
                       "bonus": r2(bonus), "promo": r2(promo)})

    denominator = net_total + bonus_total + promo_total
    share_pct = (r2((bonus_total + promo_total) / denominator * 100)
                 if denominator else 0.0)
    return {"series": series,
            "totals": {"net": r2(net_total), "bonus": r2(bonus_total),
                       "promo": r2(promo_total), "share_pct": share_pct}}


@router.get("/bonus-share")
async def bonus_share(granularity: Granularity = Query("day"),
                      period: Period = Depends(get_period)) -> dict:
    """Given-away money (topup bonuses + promo credits) vs net revenue."""
    return await _bonus_share(granularity=granularity,
                              from_iso=iso(period.from_dt),
                              to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /revenue/compare
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="revenue:compare")
async def _compare() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)

    # month-to-date vs the same day-span of the previous month
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    month_span = now - month_start
    prev_month_end = min(prev_month_start + month_span, month_start)
    month_cur = await _net_sum(db, month_start, now)
    month_prev = await _net_sum(db, prev_month_start, prev_month_end)

    # week-to-date (ISO week, Monday start) vs the same span of last week
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week_span = now - week_start
    prev_week_start = week_start - timedelta(weeks=1)
    week_cur = await _net_sum(db, week_start, now)
    week_prev = await _net_sum(db, prev_week_start, prev_week_start + week_span)

    # last 12 ISO weeks, zero-filled
    wow_start = week_start - timedelta(weeks=11)
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "credit", "kind": "topup",
                    "dt": {"$type": "date", "$gte": wow_start, "$lt": now}}},
        {"$group": {"_id": bucket_expr("week"), "revenue": {"$sum": NET_AMOUNT}}},
    ]).to_list(length=None)
    by_week: dict[tuple[int, int], float] = {}
    for row in rows:
        bucket = as_utc(row.get("_id"))
        if not isinstance(bucket, datetime):
            continue
        year, week, _ = bucket.isocalendar()
        by_week[(year, week)] = by_week.get((year, week), 0.0) + _num(
            row.get("revenue"))
    wow = []
    for i in range(12):
        start = wow_start + timedelta(weeks=i)
        year, week, _ = start.isocalendar()
        wow.append({"week": f"{year}-W{week:02d}",
                    "revenue": r2(by_week.get((year, week), 0.0))})

    return {
        "month": {"current": month_cur, "previous": month_prev,
                  "change_pct": _change_pct(month_cur, month_prev)},
        "week": {"current": week_cur, "previous": week_prev,
                 "change_pct": _change_pct(week_cur, week_prev)},
        "wow": wow,
    }


@router.get("/compare")
async def compare() -> dict:
    """Month-to-date and week-to-date NET revenue vs the previous spans."""
    return await _compare()
