"""Users router: segments, segment flows, retention cohorts, funnel, churn,
search and the per-user card.

Heavy aggregations run in MongoDB over ``users_flat`` / ``transactions_flat``;
Python only folds small intermediate results (sankey links, cohort maps,
medians). The per-user card is the single endpoint allowed to touch the raw
``users`` collection — and only to read the ``logs`` tail, masked through
``mask_free_text`` before leaving the API.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..agg import as_utc, bucket_expr, iso, r2
from ..cache import cached
from ..config import get_settings
from ..db import TX_FLAT, USERS_FLAT, get_db
from ..masking import mask_free_text, mask_payout_entry
from ..normalizer import parse_dt
from .deps import Period, get_period

log = logging.getLogger("app.users")
router = APIRouter(prefix="/users", tags=["users"])

_FLOW_MAX_NODES = 12
_SEARCH_LIMIT = 20
_CARD_TX_LIMIT = 200
_CARD_REFERRALS_LIMIT = 50
_CARD_LOGS_LIMIT = 30
_CARD_LOGS_SCAN = 300  # raw log entries inspected while collecting the tail
_AT_RISK_LIMIT = 50

_RENEWAL_MATCH: dict[str, Any] = {"direction": "debit", "kind": "renewal"}


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------

def _period_of(from_iso: Optional[str], to_iso: Optional[str]) -> Period:
    """Rebuild a Period from the ISO strings a cached helper was keyed on."""
    to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(timezone.utc)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=timezone.utc)
    from_dt = datetime.fromisoformat(from_iso) if from_iso else None
    if from_dt is not None and from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=timezone.utc)
    return Period(from_dt, to_dt)


def _in_period(dt: datetime, period: Period) -> bool:
    return (period.from_dt is None or dt >= period.from_dt) and dt < period.to_dt


def _month_idx(dt: datetime) -> int:
    return dt.year * 12 + (dt.month - 1)


def _month_label(idx: int) -> str:
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _entry_dt(value: Any) -> Optional[datetime]:
    return as_utc(value) if isinstance(value, datetime) else None


def _jsonable(value: Any) -> Any:
    """Defensive JSON coercion for raw-ish documents (ObjectId & co → str)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return iso(as_utc(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ---------------------------------------------------------------------------
# GET /users/segments — cached 60s
# ---------------------------------------------------------------------------

@cached(ttl=60, prefix="users:segments")
async def _segments() -> dict[str, Any]:
    db = get_db()
    rows = await db[USERS_FLAT].aggregate([
        {"$group": {"_id": {"$ifNull": ["$segment", "unknown"]},
                    "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]).to_list(None)
    segments = [{"segment": str(row.get("_id")), "count": int(row.get("count") or 0)}
                for row in rows]
    return {"segments": segments, "total": sum(s["count"] for s in segments)}


@router.get("/segments")
async def segments() -> dict[str, Any]:
    return await _segments()


# ---------------------------------------------------------------------------
# GET /users/segment-flows — cached 300s
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="users:segment-flows")
async def _segment_flows(from_iso: Optional[str], to_iso: Optional[str]) -> dict[str, Any]:
    period = _period_of(from_iso, to_iso)
    db = get_db()

    links: Counter[tuple[str, str]] = Counter()
    cursor = db[USERS_FLAT].find(
        {"segment_history.1": {"$exists": True}},
        {"segment_history": 1},
    ).batch_size(500)
    async for doc in cursor:
        history = doc.get("segment_history")
        if not isinstance(history, list):
            continue
        prev_seg: Optional[str] = None
        for entry in history:
            if not isinstance(entry, dict):
                continue
            seg = entry.get("segment")
            if not isinstance(seg, str) or not seg:
                continue
            dt = _entry_dt(entry.get("dt"))
            if (prev_seg is not None and seg != prev_seg
                    and dt is not None and _in_period(dt, period)):
                links[(prev_seg, seg)] += 1
            prev_seg = seg

    freq: Counter[str] = Counter()
    for (src, tgt), value in links.items():
        freq[src] += value
        freq[tgt] += value
    nodes = [name for name, _ in freq.most_common(_FLOW_MAX_NODES)]
    kept = set(nodes)

    out_links = [
        {"source": src, "target": tgt, "value": int(value)}
        for (src, tgt), value in links.most_common()
        if src in kept and tgt in kept
    ]
    return {"nodes": nodes, "links": out_links}


@router.get("/segment-flows")
async def segment_flows(period: Period = Depends(get_period)) -> dict[str, Any]:
    return await _segment_flows(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /users/retention-cohorts — cached 300s
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="users:retention-cohorts")
async def _retention_cohorts(months: int) -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    cur_idx = _month_idx(now)
    start_idx = cur_idx - (months - 1)
    window_start = datetime(start_idx // 12, start_idx % 12 + 1, 1,
                            tzinfo=timezone.utc)

    # cohort assignment: user → month index of joined_at
    cohort_of: dict[Any, int] = {}
    sizes: Counter[int] = Counter()
    cursor = db[USERS_FLAT].find(
        {"joined_at": {"$type": "date", "$gte": window_start}},
        {"joined_at": 1},
    ).batch_size(1000)
    async for doc in cursor:
        joined = _entry_dt(doc.get("joined_at"))
        if joined is None:
            continue
        c_idx = _month_idx(joined)
        cohort_of[doc.get("_id")] = c_idx
        sizes[c_idx] += 1

    # distinct (user, month) renewal debits inside the cohort window
    rows = await db[TX_FLAT].aggregate([
        {"$match": {**_RENEWAL_MATCH,
                    "dt": {"$type": "date", "$gte": window_start}}},
        {"$group": {"_id": {"user": "$user_id", "month": bucket_expr("month")}}},
    ]).to_list(None)

    hits: Counter[tuple[int, int]] = Counter()
    for row in rows:
        key = row.get("_id") or {}
        c_idx = cohort_of.get(key.get("user"))
        month = _entry_dt(key.get("month"))
        if c_idx is None or month is None:
            continue
        offset = _month_idx(month) - c_idx
        if 0 <= offset <= cur_idx - c_idx:
            hits[(c_idx, offset)] += 1

    cohorts = []
    for c_idx in sorted(sizes):
        size = sizes[c_idx]
        retention = [r2(hits[(c_idx, offset)] / size * 100)
                     for offset in range(cur_idx - c_idx + 1)]
        cohorts.append({"cohort": _month_label(c_idx), "size": int(size),
                        "retention": retention})
    return {"cohorts": cohorts}


@router.get("/retention-cohorts")
async def retention_cohorts(
    months: int = Query(12, ge=1, le=36, description="How many joined-month cohorts"),
) -> dict[str, Any]:
    return await _retention_cohorts(months=months)


# ---------------------------------------------------------------------------
# GET /users/funnel — cached 300s
# ---------------------------------------------------------------------------

def _hours_expr(start: str, end: str) -> dict[str, Any]:
    """Hours between two date fields, null when either is missing/null."""
    both_dates = {"$and": [
        {"$eq": [{"$type": start}, "date"]},
        {"$eq": [{"$type": end}, "date"]},
    ]}
    return {"$cond": [both_dates,
                      {"$divide": [{"$subtract": [end, start]}, 3_600_000]},
                      None]}


def _is_date(field: str) -> dict[str, Any]:
    return {"$cond": [{"$eq": [{"$type": field}, "date"]}, 1, 0]}


def _median_hours(values: Any) -> Optional[float]:
    nums = [float(v) for v in (values or [])
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0]
    return float(statistics.median(nums)) if nums else None


@cached(ttl=300, prefix="users:funnel")
async def _funnel(from_iso: Optional[str], to_iso: Optional[str]) -> dict[str, Any]:
    period = _period_of(from_iso, to_iso)
    db = get_db()
    joined_cond = dict(period.match("joined_at")["joined_at"])
    joined_cond["$type"] = "date"

    rows = await db[USERS_FLAT].aggregate([
        {"$match": {"joined_at": joined_cond}},
        {"$group": {
            "_id": None,
            "registrations": {"$sum": 1},
            "first_topup": {"$sum": _is_date("$first_topup_at")},
            "first_sub": {"$sum": _is_date("$first_sub_at")},
            "renewal": {"$sum": _is_date("$second_sub_at")},
            "h_topup": {"$push": _hours_expr("$joined_at", "$first_topup_at")},
            "h_sub": {"$push": _hours_expr("$first_topup_at", "$first_sub_at")},
            "h_renewal": {"$push": _hours_expr("$first_sub_at", "$second_sub_at")},
        }},
    ]).to_list(1)
    agg = rows[0] if rows else {}

    plan: list[tuple[str, int, Optional[float]]] = [
        ("registration", int(agg.get("registrations") or 0), None),
        ("first_topup", int(agg.get("first_topup") or 0),
         _median_hours(agg.get("h_topup"))),
        ("first_sub", int(agg.get("first_sub") or 0),
         _median_hours(agg.get("h_sub"))),
        ("renewal", int(agg.get("renewal") or 0),
         _median_hours(agg.get("h_renewal"))),
    ]

    steps: list[dict[str, Any]] = []
    prev: Optional[int] = None
    for name, count, median in plan:
        if prev is None:
            pct = 100.0 if count > 0 else None
        else:
            pct = r2(count / prev * 100) if prev > 0 else None
        steps.append({
            "step": name,
            "users": count,
            "conversion_pct": pct,
            "median_hours_from_prev": r2(median) if median is not None else None,
        })
        prev = count
    return {"steps": steps}


@router.get("/funnel")
async def funnel(period: Period = Depends(get_period)) -> dict[str, Any]:
    return await _funnel(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /users/churn — cached 300s
# ---------------------------------------------------------------------------

def _is_expired_segment(segment: str) -> bool:
    return "expired" in segment.lower()


@cached(ttl=300, prefix="users:churn")
async def _churn() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    cur_idx = _month_idx(now)

    joined_by_month: Counter[int] = Counter()
    churned_by_month: Counter[int] = Counter()   # distinct users churned in M
    first_churn_by_month: Counter[int] = Counter()

    cursor = db[USERS_FLAT].find(
        {}, {"segment_history": 1, "joined_at": 1},
    ).batch_size(500)
    async for doc in cursor:
        joined = _entry_dt(doc.get("joined_at"))
        if joined is not None:
            joined_by_month[_month_idx(joined)] += 1

        history = doc.get("segment_history")
        if not isinstance(history, list):
            continue
        churn_months: set[int] = set()
        prev_seg: Optional[str] = None
        for entry in history:
            if not isinstance(entry, dict):
                continue
            seg = entry.get("segment")
            if not isinstance(seg, str) or not seg:
                continue
            dt = _entry_dt(entry.get("dt"))
            if (prev_seg is not None and dt is not None
                    and _is_expired_segment(seg)
                    and not _is_expired_segment(prev_seg)):
                churn_months.add(_month_idx(dt))
            prev_seg = seg
        if churn_months:
            for m_idx in churn_months:
                churned_by_month[m_idx] += 1
            first_churn_by_month[min(churn_months)] += 1

    monthly: list[dict[str, Any]] = []
    if churned_by_month:
        start = min(churned_by_month)
        # active_start ≈ joined before month M and not yet churned (approximation)
        joins_before = sum(v for idx, v in joined_by_month.items() if idx < start)
        churned_before = sum(v for idx, v in first_churn_by_month.items()
                             if idx < start)
        for m_idx in range(start, cur_idx + 1):
            active_start = max(joins_before - churned_before, 0)
            churned = int(churned_by_month.get(m_idx, 0))
            rate = r2(churned / active_start * 100) if active_start > 0 else None
            monthly.append({
                "month": _month_label(m_idx),
                "churned": churned,
                "active_start": active_start,
                "churn_rate_pct": rate,
            })
            joins_before += joined_by_month.get(m_idx, 0)
            churned_before += first_churn_by_month.get(m_idx, 0)

    at_risk_query: dict[str, Any] = {
        "days_to_expire": {"$lt": 3},
        "balance": {"$lt": 150},
        "segment": {"$not": {"$regex": "expired"}},
    }
    at_risk_count = await db[USERS_FLAT].count_documents(at_risk_query)
    at_risk_rows = await db[USERS_FLAT].find(
        at_risk_query,
        {"username": 1, "segment": 1, "days_to_expire": 1,
         "balance": 1, "renewals_count": 1},
    ).sort("days_to_expire", 1).limit(_AT_RISK_LIMIT).to_list(_AT_RISK_LIMIT)

    at_risk = [{
        "user_id": row.get("_id"),
        "username": row.get("username"),
        "segment": row.get("segment"),
        "days_to_expire": (float(row["days_to_expire"])
                           if isinstance(row.get("days_to_expire"), (int, float))
                           else None),
        "balance": r2(row.get("balance")),
        "renewals_count": int(row.get("renewals_count") or 0),
    } for row in at_risk_rows]

    return {"monthly": monthly, "at_risk": at_risk,
            "at_risk_count": int(at_risk_count)}


@router.get("/churn")
async def churn() -> dict[str, Any]:
    return await _churn()


# ---------------------------------------------------------------------------
# GET /users/search — live, NOT cached
# ---------------------------------------------------------------------------

@router.get("/search")
async def search_users(
    q: str = Query("", description="Telegram id or username substring"),
) -> dict[str, Any]:
    query = q.strip()
    if not query:
        return {"results": []}

    conditions: list[dict[str, Any]] = [
        {"username_lower": {"$regex": re.escape(query.lower())}},
    ]
    if query.isdigit():
        conditions.append({"_id": int(query)})

    db = get_db()
    rows = await db[USERS_FLAT].find(
        {"$or": conditions},
        {"username": 1, "segment": 1, "joined_at": 1,
         "topup_total": 1, "last_tx_at": 1},
    ).sort("topup_total", -1).limit(_SEARCH_LIMIT).to_list(_SEARCH_LIMIT)

    return {"results": [{
        "user_id": row.get("_id"),
        "username": row.get("username"),
        "segment": row.get("segment"),
        "joined_at": iso(_entry_dt(row.get("joined_at"))),
        "topup_total": r2(row.get("topup_total")),
        "last_tx_at": iso(_entry_dt(row.get("last_tx_at"))),
    } for row in rows]}


# ---------------------------------------------------------------------------
# GET /users/{user_id}/card — live, NOT cached; 404 when unknown
# ---------------------------------------------------------------------------

_LOG_DT_KEYS = ("dt", "date", "ts", "time", "at")
_LOG_TEXT_KEYS = ("text", "msg", "message", "action", "event", "desc", "log")


def _parse_log_entry(entry: Any) -> Optional[dict[str, Any]]:
    """Best-effort ``{"dt", "text"}`` from one raw log entry (masked)."""
    dt: Optional[datetime] = None
    text: Optional[str] = None
    if isinstance(entry, dict):
        for key in _LOG_DT_KEYS:
            dt = parse_dt(entry.get(key))
            if dt is not None:
                break
        for key in _LOG_TEXT_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                text = value
                break
        if text is None:
            parts = [str(v) for k, v in entry.items()
                     if k not in _LOG_DT_KEYS
                     and isinstance(v, (str, int, float))
                     and not isinstance(v, bool)]
            joined = " ".join(p for p in parts if p.strip())
            text = joined or None
    elif isinstance(entry, (list, tuple)):
        for element in entry:
            if dt is None:
                parsed = parse_dt(element)
                if parsed is not None:
                    dt = parsed
                    continue
            if text is None and isinstance(element, str) and element.strip():
                text = element
    elif isinstance(entry, str) and entry.strip():
        dt = parse_dt(entry)
        if dt is None:
            text = entry

    if dt is None and text is None:
        return None
    return {"dt": iso(as_utc(dt)), "text": mask_free_text(text or "")}


async def _recent_logs(user_id: int) -> list[dict[str, Any]]:
    """Tail of the raw user document's free-form ``logs`` (masked)."""
    db = get_db()
    raw_users = db[get_settings().users_collection]
    try:
        raw = await raw_users.find_one({"$or": [
            {"tg_id": user_id}, {"tg_id": str(user_id)},
            {"telegram_id": user_id}, {"telegram_id": str(user_id)},
            {"user_id": user_id}, {"user_id": str(user_id)},
            {"_id": user_id}, {"_id": str(user_id)},
        ]}, {"logs": 1})
    except Exception:
        log.warning("raw users lookup failed", exc_info=True,
                    extra={"user_id": user_id})
        return []
    if not raw:
        return []

    logs = raw.get("logs")
    if isinstance(logs, dict):
        logs = list(logs.values())
    if not isinstance(logs, (list, tuple)):
        return []

    parsed: list[dict[str, Any]] = []
    for entry in reversed(logs[-_CARD_LOGS_SCAN:]):
        item = _parse_log_entry(entry)
        if item is not None:
            parsed.append(item)
        if len(parsed) >= _CARD_LOGS_LIMIT:
            break
    parsed.reverse()  # back to source (chronological) order
    return parsed


@router.get("/{user_id}/card")
async def user_card(user_id: int) -> dict[str, Any]:
    db = get_db()
    user = await db[USERS_FLAT].find_one({"_id": user_id})
    if user is None:
        raise HTTPException(404, f"User {user_id} not found")

    ref_stats = user.get("ref_stats")
    if isinstance(ref_stats, dict):
        history = ref_stats.get("payout_history")
        ref_stats["payout_history"] = ([mask_payout_entry(e) for e in history]
                                       if isinstance(history, list) else [])

    tx_rows = await db[TX_FLAT].find(
        {"user_id": user_id},
        {"dt": 1, "direction": 1, "kind": 1, "amount": 1,
         "source": 1, "bonus": 1, "promo_code": 1, "desc": 1},
    ).sort("dt", -1).limit(_CARD_TX_LIMIT).to_list(_CARD_TX_LIMIT)
    transactions = [{
        "dt": iso(_entry_dt(row.get("dt"))),
        "direction": row.get("direction"),
        "kind": row.get("kind"),
        "amount": r2(row.get("amount")),
        "source": row.get("source"),
        "bonus": r2(row.get("bonus")),
        "promo_code": row.get("promo_code"),
        "desc": row.get("desc"),
    } for row in tx_rows]

    referral_rows = await db[USERS_FLAT].find(
        {"referrer_id": {"$in": [user_id, str(user_id)]}},
        {"username": 1, "topup_total": 1, "joined_at": 1},
    ).limit(_CARD_REFERRALS_LIMIT).to_list(_CARD_REFERRALS_LIMIT)
    referrals = [{
        "user_id": row.get("_id"),
        "username": row.get("username"),
        "topup_total": r2(row.get("topup_total")),
        "joined_at": iso(_entry_dt(row.get("joined_at"))),
    } for row in referral_rows]

    return {
        "user": _jsonable(user),
        "transactions": transactions,
        "referrals": referrals,
        "recent_logs": await _recent_logs(user_id),
    }
