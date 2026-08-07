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
from ..db import PAYMENTS_FLAT, TX_FLAT, USERS_FLAT, get_db
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

async def _provider_agg(db: Any, coll: str, match: dict, now: datetime) -> list[dict]:
    """Per-source payment stream stats (shared by both data sources)."""
    return await db[coll].aggregate([
        {"$match": match},
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


@cached(ttl=60, prefix="overview:providers-status")
async def _providers_status() -> dict[str, Any]:
    db = get_db()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=_PROVIDER_WINDOW_DAYS)

    # Primary source: provider webhooks mirrored into payments_flat — they
    # carry every acquiring event with clean timestamps and commissions.
    # Sources absent there (e.g. old providers) fall back to balance credits.
    webhook_rows = await _provider_agg(db, PAYMENTS_FLAT, {
        "status": "paid", "source": {"$type": "string"},
        "dt": {"$type": "date", "$gte": since},
    }, now)
    balance_rows = await _provider_agg(db, TX_FLAT, {
        **_TOPUP_MATCH, "source": {"$type": "string"},
        "dt": {"$type": "date", "$gte": since},
    }, now)

    webhook_sources = {str(r.get("_id")) for r in webhook_rows}
    rows = webhook_rows + [r for r in balance_rows
                           if str(r.get("_id")) not in webhook_sources]

    # commissions and failures per source over the webhook stream
    extras: dict[str, dict[str, float]] = {}
    if webhook_rows:
        async for row in db[PAYMENTS_FLAT].aggregate([
            {"$match": {"source": {"$type": "string"},
                        "dt": {"$type": "date",
                               "$gte": now - timedelta(days=30)}}},
            {"$group": {
                "_id": "$source",
                "commission_30d": {"$sum": {"$cond": [
                    {"$eq": ["$status", "paid"]},
                    {"$ifNull": ["$commission", 0]}, 0]}},
                "failed_24h": {"$sum": {"$cond": [
                    {"$and": [{"$eq": ["$status", "failed"]},
                              {"$gte": ["$dt", now - timedelta(hours=24)]}]},
                    1, 0]}},
            }},
        ]):
            extras[str(row.get("_id"))] = {
                "commission_30d": r2(row.get("commission_30d")),
                "failed_24h": int(row.get("failed_24h") or 0),
            }

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

        source = str(row.get("_id"))
        providers.append({
            "source": source,
            "last_payment_at": iso(last_at),
            "payments_24h": int(row.get("payments_24h") or 0),
            "payments_7d": int(row.get("payments_7d") or 0),
            "median_gap_hours": r2(median_gap),
            "silence_hours": r2(silence),
            "threshold_hours": r2(threshold),
            "status": status,
            "via": "webhook" if source in webhook_sources else "balance",
            "commission_30d": extras.get(source, {}).get("commission_30d"),
            "failed_24h": extras.get(source, {}).get("failed_24h"),
        })
    providers.sort(key=lambda p: p["source"])
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


# ---------------------------------------------------------------------------
# GET /overview/pace — сегодняшний темп выручки и разбор отклонения
# ---------------------------------------------------------------------------

_PACE_BASELINE_DAYS = 28
_PACE_BAND_PCT = 15.0  # ±15% считается нормой


def _cum_at_cutoff(by_hour: dict[int, float], hour: int, frac: float) -> float:
    """Cumulative value up to `hour` plus `frac` of that hour's bucket."""
    total = sum(v for h, v in by_hour.items() if h < hour)
    return total + by_hour.get(hour, 0.0) * frac


def _median_or_zero(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _pace_baseline_days(today: date) -> set[str]:
    """Baseline: last 7 days + the same weekday of the past 4 weeks."""
    days: set[str] = set()
    for i in range(1, 8):
        days.add((today - timedelta(days=i)).isoformat())
    for w in range(1, 5):
        days.add((today - timedelta(days=7 * w)).isoformat())
    return days


@cached(ttl=60, prefix="overview:pace")
async def _pace() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    baseline_start = today_start - timedelta(days=_PACE_BASELINE_DAYS)
    cutoff_hour = now.hour
    cutoff_frac = now.minute / 60.0
    today_key = today_start.date().isoformat()
    baseline_keys = _pace_baseline_days(today_start.date())

    # --- top-ups per (day, hour, source) --------------------------------
    topup_rows = await db[TX_FLAT].aggregate([
        {"$match": {**_TOPUP_MATCH, "dt": {"$type": "date",
                                           "$gte": baseline_start}}},
        {"$group": {
            "_id": {"day": {"$dateTrunc": {"date": "$dt", "unit": "day"}},
                    "hour": {"$hour": "$dt"},
                    "source": {"$ifNull": ["$source", "other"]}},
            "net": {"$sum": NET_AMOUNT},
            "count": {"$sum": 1},
        }},
    ]).to_list(length=None)

    # --- renewals per (day, hour) ---------------------------------------
    renewal_rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "debit", "kind": "renewal",
                    "dt": {"$type": "date", "$gte": baseline_start}}},
        {"$group": {"_id": {"day": {"$dateTrunc": {"date": "$dt",
                                                   "unit": "day"}},
                            "hour": {"$hour": "$dt"}},
                    "count": {"$sum": 1}}},
    ]).to_list(length=None)

    # --- registrations per (day, hour) ----------------------------------
    reg_rows = await db[USERS_FLAT].aggregate([
        {"$match": {"joined_at": {"$type": "date", "$gte": baseline_start}}},
        {"$group": {"_id": {"day": {"$dateTrunc": {"date": "$joined_at",
                                                   "unit": "day"}},
                            "hour": {"$hour": "$joined_at"}},
                    "count": {"$sum": 1}}},
    ]).to_list(length=None)

    # --- fold into per-day hourly profiles ------------------------------
    net_by_day: dict[str, dict[int, float]] = {}
    cnt_by_day: dict[str, dict[int, float]] = {}
    src_by_day: dict[str, dict[str, dict[int, float]]] = {}
    for row in topup_rows:
        key = row.get("_id") or {}
        day = _day_key(key.get("day"))
        if day is None:
            continue
        hour = int(key.get("hour") or 0)
        source = str(key.get("source") or "other")
        net = float(row.get("net") or 0)
        net_by_day.setdefault(day, {})
        net_by_day[day][hour] = net_by_day[day].get(hour, 0.0) + net
        cnt_by_day.setdefault(day, {})
        cnt_by_day[day][hour] = (cnt_by_day[day].get(hour, 0.0)
                                 + float(row.get("count") or 0))
        src_by_day.setdefault(day, {}).setdefault(source, {})
        src_by_day[day][source][hour] = (
            src_by_day[day][source].get(hour, 0.0) + net)

    def hourly_counts(rows: list[dict]) -> dict[str, dict[int, float]]:
        out: dict[str, dict[int, float]] = {}
        for row in rows:
            key = row.get("_id") or {}
            day = _day_key(key.get("day"))
            if day is None:
                continue
            hour = int(key.get("hour") or 0)
            out.setdefault(day, {})
            out[day][hour] = out[day].get(hour, 0.0) + float(
                row.get("count") or 0)
        return out

    renewals_by_day = hourly_counts(renewal_rows)
    regs_by_day = hourly_counts(reg_rows)

    base_days = [d for d in baseline_keys if d in net_by_day
                 or d in regs_by_day or d in renewals_by_day]

    def baseline_median(profiles: dict[str, dict[int, float]],
                        full_day: bool = False) -> float:
        values = []
        for day in base_days:
            by_hour = profiles.get(day, {})
            values.append(sum(by_hour.values()) if full_day
                          else _cum_at_cutoff(by_hour, cutoff_hour,
                                              cutoff_frac))
        return _median_or_zero(values)

    today_net = net_by_day.get(today_key, {})
    today_so_far = round(sum(today_net.values()), 2)
    expected_so_far = round(baseline_median(net_by_day), 2)
    expected_full = round(baseline_median(net_by_day, full_day=True), 2)

    deviation_pct = (round((today_so_far - expected_so_far)
                           / expected_so_far * 100, 1)
                     if expected_so_far > 0 else None)
    projected = (round(today_so_far / expected_so_far * expected_full, 2)
                 if expected_so_far > 0 and expected_full > 0 else None)

    if not base_days or expected_so_far <= 0:
        status = "no_data"
    elif deviation_pct is not None and deviation_pct < -_PACE_BAND_PCT:
        status = "behind"
    elif deviation_pct is not None and deviation_pct > _PACE_BAND_PCT:
        status = "ahead"
    else:
        status = "on_track"

    # --- hourly cumulative series for the chart -------------------------
    series = []
    for hour in range(24):
        expected_cum = _median_or_zero([
            _cum_at_cutoff(net_by_day.get(d, {}), hour, 1.0)
            for d in base_days])
        today_cum = (round(_cum_at_cutoff(today_net, hour, 1.0), 2)
                     if hour <= cutoff_hour else None)
        series.append({"hour": hour, "today": today_cum,
                       "expected": round(expected_cum, 2)})

    # --- factor decomposition -------------------------------------------
    def factor(key: str, label: str, unit: str, today_value: float,
               expected_value: float) -> dict[str, Any]:
        delta = (round((today_value - expected_value) / expected_value * 100, 1)
                 if expected_value > 0 else None)
        return {"key": key, "label": label, "unit": unit,
                "today": round(today_value, 2),
                "expected": round(expected_value, 2),
                "delta_pct": delta}

    today_cnt = sum(cnt_by_day.get(today_key, {}).values())
    exp_cnt = baseline_median(cnt_by_day)
    today_check = today_so_far / today_cnt if today_cnt else 0.0
    check_baseline = []
    for day in base_days:
        c = _cum_at_cutoff(cnt_by_day.get(day, {}), cutoff_hour, cutoff_frac)
        n = _cum_at_cutoff(net_by_day.get(day, {}), cutoff_hour, cutoff_frac)
        if c > 0:
            check_baseline.append(n / c)
    exp_check = _median_or_zero(check_baseline)

    factors = [
        factor("payments_count", "Число пополнений", "шт",
               today_cnt, exp_cnt),
        factor("avg_check", "Средний чек", "₽", today_check, exp_check),
        factor("renewals", "Продления (списания)", "шт",
               sum(renewals_by_day.get(today_key, {}).values()),
               baseline_median(renewals_by_day)),
        factor("registrations", "Регистрации", "шт",
               sum(regs_by_day.get(today_key, {}).values()),
               baseline_median(regs_by_day)),
    ]

    # per-source contributions, biggest absolute gap first
    sources = {s for day in base_days
               for s in src_by_day.get(day, {})} | set(
                   src_by_day.get(today_key, {}))
    source_factors = []
    for source in sources:
        expected_src = _median_or_zero([
            _cum_at_cutoff(src_by_day.get(d, {}).get(source, {}),
                           cutoff_hour, cutoff_frac) for d in base_days])
        today_src = sum(src_by_day.get(today_key, {}).get(source, {}).values())
        if expected_src < 1 and today_src < 1:
            continue
        f = factor(f"source:{source}", source, "₽", today_src, expected_src)
        f["gap_rub"] = round(today_src - expected_src, 2)
        source_factors.append(f)
    source_factors.sort(key=lambda f: abs(f.get("gap_rub") or 0),
                        reverse=True)
    factors.extend(source_factors[:5])

    # --- human verdict ---------------------------------------------------
    verdict = ""
    if status == "no_data":
        verdict = "Мало истории для оценки — нужен хотя бы день данных."
    elif status == "on_track":
        verdict = "Темп в пределах нормы (±15% от обычного к этому часу)."
    else:
        word = "Отстаём" if status == "behind" else "Опережаем"
        reasons = []
        for f in factors:
            d = f.get("delta_pct")
            if d is None:
                continue
            same_sign = (d < 0) == (status == "behind")
            if abs(d) >= 20 and same_sign and f["key"] != "avg_check":
                reasons.append(f"{f['label'].lower()}: {d:+.0f}%")
            elif abs(d) >= 20 and same_sign:
                reasons.append(f"средний чек: {d:+.0f}%")
            if len(reasons) == 2:
                break
        verdict = (f"{word} на {abs(deviation_pct):.0f}% от обычного темпа"
                   + (": " + "; ".join(reasons) if reasons else "."))

    return {
        "now_hour": cutoff_hour,
        "baseline_days": len(base_days),
        "today_so_far": today_so_far,
        "expected_so_far": expected_so_far,
        "expected_full_day": expected_full,
        "projected_today": projected,
        "deviation_pct": deviation_pct,
        "status": status,
        "series": series,
        "factors": factors,
        "verdict": verdict,
    }


@router.get("/pace")
async def pace() -> dict[str, Any]:
    """Сегодняшняя выручка против обычного темпа + факторы отклонения."""
    return await _pace()


# ---------------------------------------------------------------------------
# GET /overview/renewal-outlook — кто истекает сегодня и сколько это денег
# ---------------------------------------------------------------------------

_OUTLOOK_HISTORY_DAYS = 14
_EXPIRED_RE = "expired|churn"
_ACTIVE_RE = "active"


async def _personal_costs(db: Any, user_ids: list[int]) -> dict[int, float]:
    """Per-user renewal spend over the last 30 days (their real plan price
    regardless of billing granularity)."""
    if not user_ids:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=30)
    rows = await db[TX_FLAT].aggregate([
        {"$match": {"direction": "debit", "kind": "renewal",
                    "user_id": {"$in": user_ids[:2000]},
                    "dt": {"$type": "date", "$gte": since}}},
        {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}},
    ]).to_list(length=None)
    return {int(r["_id"]): float(r["total"] or 0) for r in rows
            if r.get("_id") is not None}


@cached(ttl=120, prefix="overview:renewal-outlook")
async def _renewal_outlook() -> dict[str, Any]:
    from .experiments import _monthly_sub_cost  # shared median

    db = get_db()
    settings = get_settings()
    min_topup = float(settings.min_topup_rub)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    monthly_cost = await _monthly_sub_cost(db, 199.0)

    # --- expiring today --------------------------------------------------
    expiring_docs = await db[USERS_FLAT].find(
        {"$or": [
            {"sub_until": {"$gte": today_start, "$lt": tomorrow}},
            {"sub_until": None, "days_to_expire": {"$gte": 0, "$lt": 1}},
        ]},
        {"username": 1, "balance": 1, "days_to_expire": 1, "sub_until": 1,
         "renewals_count": 1, "segment": 1},
    ).limit(2000).to_list(length=None)

    costs = await _personal_costs(db, [int(d["_id"]) for d in expiring_docs])

    users = []
    can_renew = 0
    need_topup = 0
    potential_topup = 0.0
    potential_monthly = 0.0
    for doc in expiring_docs:
        uid = int(doc["_id"])
        balance = float(doc.get("balance") or 0)
        personal = costs.get(uid) or monthly_cost
        potential_monthly += personal
        needed = max(0.0, personal - balance)
        if needed <= 0:
            can_renew += 1
            expected = 0.0
        else:
            need_topup += 1
            expected = max(needed, min_topup)
            potential_topup += expected
        users.append({
            "user_id": uid,
            "username": doc.get("username"),
            "segment": doc.get("segment"),
            "balance": r2(balance),
            "personal_cost": r2(personal),
            "needed": r2(needed),
            "expected_topup": r2(expected),
            "sub_until": iso(as_utc(doc.get("sub_until"))),
        })
    users.sort(key=lambda u: -u["expected_topup"])

    # --- who we lost over the last days ---------------------------------
    since = today_start - timedelta(days=_OUTLOOK_HISTORY_DAYS)
    churned_by_day: dict[str, int] = {}
    returned_by_day: dict[str, int] = {}
    import re as _re
    expired_re = _re.compile(_EXPIRED_RE, _re.IGNORECASE)
    active_re = _re.compile(_ACTIVE_RE, _re.IGNORECASE)
    cursor = db[USERS_FLAT].find(
        {"segment_history": {"$elemMatch": {"dt": {"$gte": since}}}},
        {"segment_history": 1},
    ).batch_size(500)
    async for doc in cursor:
        history = doc.get("segment_history") or []
        for idx, entry in enumerate(history):
            dt = as_utc(entry.get("dt")) if isinstance(entry, dict) else None
            segment = (entry or {}).get("segment") if isinstance(entry, dict) else None
            if dt is None or not segment or dt < since:
                continue
            prev = history[idx - 1].get("segment") if idx else None
            day = dt.date().isoformat()
            if expired_re.search(str(segment)) and not (
                    prev and expired_re.search(str(prev))):
                churned_by_day[day] = churned_by_day.get(day, 0) + 1
                # came back later?
                if any(isinstance(later, dict)
                       and active_re.search(str(later.get("segment") or ""))
                       for later in history[idx + 1:]):
                    returned_by_day[day] = returned_by_day.get(day, 0) + 1

    history_series = []
    for i in range(_OUTLOOK_HISTORY_DAYS, -1, -1):
        day = (today_start - timedelta(days=i)).date().isoformat()
        churned = churned_by_day.get(day, 0)
        history_series.append({
            "day": day,
            "churned": churned,
            "returned": returned_by_day.get(day, 0),
            "lost_monthly_rub": r2(churned * monthly_cost),
        })

    return {
        "min_topup": min_topup,
        "monthly_sub_cost": r2(monthly_cost),
        "today": {
            "expiring": len(users),
            "can_renew_from_balance": can_renew,
            "need_topup": need_topup,
            "potential_topup_rub": r2(potential_topup),
            "potential_monthly_rub": r2(potential_monthly),
            "users": users[:20],
        },
        "history": history_series,
    }


@router.get("/renewal-outlook")
async def renewal_outlook() -> dict[str, Any]:
    """Истекающие сегодня подписки: ожидаемые пополнения и отвал за 2 недели."""
    return await _renewal_outlook()
