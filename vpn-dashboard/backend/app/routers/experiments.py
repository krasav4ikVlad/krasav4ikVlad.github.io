"""A/B experiment analytics and quantified growth opportunities.

The bot maintains ``growth.ab_group`` / ``growth.trial_ab_group`` per user;
this router turns them into comparable metrics with a two-proportion z-test
against the control (largest) group, and estimates the ruble impact of
standard revenue levers (expiring users, winback, trial activation, upsell).
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from ..agg import as_utc, iso, r2
from ..cache import cached
from ..db import TX_FLAT, USERS_FLAT, get_db

router = APIRouter(prefix="/experiments", tags=["experiments"])

Experiment = Literal["ab_group", "trial_ab_group"]


def two_proportion_z(successes_a: int, total_a: int,
                     successes_b: int, total_b: int
                     ) -> Optional[tuple[float, float]]:
    """Two-sided two-proportion z-test. Returns (z, p_value) or None when
    undecidable (empty group / zero variance)."""
    if total_a <= 0 or total_b <= 0:
        return None
    p_a = successes_a / total_a
    p_b = successes_b / total_b
    pooled = (successes_a + successes_b) / (total_a + total_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / total_a + 1 / total_b))
    if se == 0:
        return None
    z = (p_a - p_b) / se
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return z, p_value


def _parse_dt_param(value: Optional[str], name: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, f"Invalid {name!r} datetime: {value!r}")


# ---------------------------------------------------------------------------
# GET /experiments/ab
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="experiments:ab")
async def _ab(*, experiment: str, joined_from: Optional[str],
              joined_to: Optional[str]) -> dict[str, Any]:
    db = get_db()
    match: dict[str, Any] = {}
    from_dt = _parse_dt_param(joined_from, "joined_from")
    to_dt = _parse_dt_param(joined_to, "joined_to")
    if from_dt or to_dt:
        cond: dict[str, Any] = {"$type": "date"}
        if from_dt:
            cond["$gte"] = from_dt
        if to_dt:
            cond["$lt"] = to_dt
        match["joined_at"] = cond

    rows = await db[USERS_FLAT].aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"$ifNull": [f"${experiment}", None]},
            "users": {"$sum": 1},
            "paying": {"$sum": {"$cond": [{"$gt": ["$topup_total", 0]}, 1, 0]}},
            "topup_sum": {"$sum": {"$ifNull": ["$topup_total", 0]}},
            "renewed": {"$sum": {"$cond": [
                {"$ne": ["$second_sub_at", None]}, 1, 0]}},
        }},
        {"$sort": {"users": -1}},
    ]).to_list(length=None)

    untagged = 0
    groups: list[dict[str, Any]] = []
    for row in rows:
        if row.get("_id") is None:
            untagged = int(row.get("users") or 0)
            continue
        users = int(row.get("users") or 0)
        paying = int(row.get("paying") or 0)
        topup_sum = float(row.get("topup_sum") or 0)
        renewed = int(row.get("renewed") or 0)
        groups.append({
            "group": str(row["_id"]),
            "users": users,
            "paying": paying,
            "conversion_pct": r2(paying / users * 100) if users else 0.0,
            "arpu": r2(topup_sum / users) if users else 0.0,
            "avg_ltv_paying": r2(topup_sum / paying) if paying else 0.0,
            "renewal_share_pct": r2(renewed / paying * 100) if paying else 0.0,
            "vs_control": None,
        })

    control = groups[0]["group"] if groups else None
    if control is not None:
        base = groups[0]
        for group in groups[1:]:
            test = two_proportion_z(group["paying"], group["users"],
                                    base["paying"], base["users"])
            if test is None:
                continue
            z, p_value = test
            group["vs_control"] = {
                "z": r2(z),
                "p_value": round(p_value, 4),
                "significant": p_value < 0.05,
                "conversion_diff_pp": r2(
                    group["conversion_pct"] - base["conversion_pct"]),
            }

    return {"experiment": experiment, "control": control,
            "groups": groups, "untagged_users": untagged}


@router.get("/ab")
async def ab(experiment: Experiment = Query("ab_group"),
             joined_from: Optional[str] = Query(None),
             joined_to: Optional[str] = Query(None)) -> dict[str, Any]:
    return await _ab(experiment=experiment, joined_from=joined_from,
                     joined_to=joined_to)


# ---------------------------------------------------------------------------
# GET /experiments/opportunities
# ---------------------------------------------------------------------------

async def _monthly_sub_cost(db: Any, default: float) -> float:
    """Median per-user renewal spend over the last 30 days.

    Robust to the billing model: with daily micro-charges (~4₽/день) a
    single renewal transaction is meaningless — what matters is what a
    subscriber pays per month.
    """
    since = datetime.now(timezone.utc) - timedelta(days=30)
    totals = [r["total"] async for r in db[TX_FLAT].aggregate([
        {"$match": {"direction": "debit", "kind": "renewal",
                    "amount": {"$gt": 0},
                    "dt": {"$type": "date", "$gte": since}}},
        {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}},
        {"$limit": 5000},
    ])]
    return float(statistics.median(totals)) if totals else default


async def _median_check(db: Any, default: float) -> float:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    amounts = [r["amount"] async for r in db[TX_FLAT].find(
        {"direction": "credit", "kind": "topup", "amount": {"$gt": 0},
         "dt": {"$type": "date", "$gte": since}},
        {"amount": 1}).limit(5000)]
    return float(statistics.median(amounts)) if amounts else default


@cached(ttl=300, prefix="experiments:opportunities")
async def _opportunities() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    monthly_sub_cost = await _monthly_sub_cost(db, 199.0)
    median_check = await _median_check(db, 299.0)

    uf = db[USERS_FLAT]
    expiring_no_balance = await uf.count_documents({
        "days_to_expire": {"$gt": 0, "$lt": 3},
        "balance": {"$lt": monthly_sub_cost},
        "renewals_count": {"$gt": 0},
    })
    winback = await uf.count_documents({
        "segment": {"$regex": "expired|churn", "$options": "i"},
        "topup_count": {"$gt": 0},
        "last_tx_at": {"$gte": now - timedelta(days=60),
                       "$lt": now - timedelta(days=7)},
    })
    trial_no_topup = await uf.count_documents({
        "topup_count": 0,
        "joined_at": {"$type": "date",
                      "$gte": now - timedelta(days=14),
                      "$lt": now - timedelta(days=2)},
    })
    bypass_upsell = await uf.count_documents({"bypass_count": {"$gte": 2}})
    dormant_balance = await uf.count_documents({
        "balance": {"$gte": monthly_sub_cost},
        "$or": [{"days_to_expire": {"$lte": 0}},
                {"segment": {"$regex": "expired", "$options": "i"}}],
    })

    opportunities = [
        {
            "key": "expiring_no_balance",
            "title": "Истекают в ближайшие 3 дня без денег на балансе",
            "description": "Платившие ранее юзеры, у которых подписка кончается, "
                           "а баланса на продление не хватает. Напоминание с "
                           "быстрой оплатой — самый дешёвый доход.",
            "users": expiring_no_balance,
            "potential_rub": r2(expiring_no_balance * monthly_sub_cost * 0.4),
            "assumption": "конверсия напоминания 40%",
        },
        {
            "key": "winback_expired",
            "title": "Ушли за последние 7–60 дней (winback)",
            "description": "Платившие юзеры с истёкшей подпиской. Промокод на "
                           "возврат (например, −20% на месяц) окупается почти "
                           "всегда.",
            "users": winback,
            "potential_rub": r2(winback * monthly_sub_cost * 0.12),
            "assumption": "конверсия winback-рассылки 12%",
        },
        {
            "key": "trial_no_topup",
            "title": "Зарегистрировались 2–14 дней назад и ни разу не платили",
            "description": "Горячая аудитория: онбординг-цепочка или стартовый "
                           "промокод на первое пополнение.",
            "users": trial_no_topup,
            "potential_rub": r2(trial_no_topup * median_check * 0.08),
            "assumption": "конверсия активации 8%",
        },
        {
            "key": "bypass_upsell",
            "title": "Повторные покупатели ByPass — кандидаты на большой пакет",
            "description": "Покупали трафик ≥ 2 раз: предложить пакет 20–50 ГБ "
                           "со скидкой за объём — выше средний чек и меньше "
                           "трение.",
            "users": bypass_upsell,
            "potential_rub": r2(bypass_upsell * 50 * 2),
            "assumption": "+2 покупки по 50₽ на юзера в месяц",
        },
        {
            "key": "dormant_balance",
            "title": "Подписка истекла, а на балансе лежат деньги",
            "description": "Юзер уже заплатил, но не продлился — одно касание "
                           "(«у вас хватает на продление») возвращает его почти "
                           "бесплатно.",
            "users": dormant_balance,
            "potential_rub": r2(dormant_balance * monthly_sub_cost * 0.5),
            "assumption": "конверсия напоминания 50%",
        },
    ]
    opportunities.sort(key=lambda o: -o["potential_rub"])
    return {"monthly_sub_cost": r2(monthly_sub_cost),
            "median_check": r2(median_check),
            "opportunities": opportunities,
            "computed_at": iso(as_utc(now))}


@router.get("/opportunities")
async def opportunities() -> dict[str, Any]:
    return await _opportunities()


# ---------------------------------------------------------------------------
# GET /experiments/registration-economics — сколько приносит одна регистрация
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="experiments:reg-economics")
async def _registration_economics() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    uf = db[USERS_FLAT]

    # value per registration over completed horizons only: a user counts for
    # rev_dN when their first N days are already behind them
    horizons: dict[str, dict[str, Any]] = {}
    for days, field in ((7, "rev_d7"), (30, "rev_d30"), (90, "rev_d90")):
        cutoff = now - timedelta(days=days)
        rows = await uf.aggregate([
            {"$match": {"joined_at": {"$type": "date", "$lt": cutoff},
                        field: {"$ne": None}}},
            {"$group": {
                "_id": None,
                "users": {"$sum": 1},
                "revenue": {"$sum": f"${field}"},
                "paying": {"$sum": {"$cond": [{"$gt": [f"${field}", 0]},
                                              1, 0]}},
            }},
        ]).to_list(length=1)
        row = rows[0] if rows else {}
        users = int(row.get("users") or 0)
        revenue = float(row.get("revenue") or 0)
        paying = int(row.get("paying") or 0)
        horizons[f"d{days}"] = {
            "users": users,
            "value_per_reg": r2(revenue / users) if users else 0.0,
            "paying_share_pct": r2(paying / users * 100) if users else 0.0,
            "value_per_paying": r2(revenue / paying) if paying else 0.0,
        }

    # cohort trend: monthly cohorts, avg first-30d revenue
    trend_rows = await uf.aggregate([
        {"$match": {"joined_at": {"$type": "date",
                                  "$gte": now - timedelta(days=270)},
                    "rev_d30": {"$ne": None}}},
        {"$group": {
            "_id": {"$dateTrunc": {"date": "$joined_at", "unit": "month"}},
            "users": {"$sum": 1},
            "revenue30": {"$sum": "$rev_d30"},
            "paying": {"$sum": {"$cond": [{"$gt": ["$rev_d30", 0]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]).to_list(length=None)
    complete_before = now - timedelta(days=30)
    trend = []
    for row in trend_rows:
        month = as_utc(row.get("_id"))
        if month is None:
            continue
        users = int(row.get("users") or 0)
        trend.append({
            "cohort": month.strftime("%Y-%m"),
            "users": users,
            "value_per_reg_30d": r2(float(row.get("revenue30") or 0) / users)
                if users else 0.0,
            "paying_share_pct": r2(int(row.get("paying") or 0) / users * 100)
                if users else 0.0,
            # the last month's 30d windows are still open — value keeps growing
            "complete": month < complete_before - timedelta(days=31),
        })

    # current registration rate
    regs_14d = await uf.count_documents(
        {"joined_at": {"$type": "date", "$gte": now - timedelta(days=14)}})
    regs_per_day = round(regs_14d / 14, 1)

    # churn to offset: users who slid into expired-like segments in 30 days
    monthly_cost = await _monthly_sub_cost(db, 199.0)
    since = now - timedelta(days=30)
    churned = 0
    cursor = uf.find(
        {"segment_history": {"$elemMatch": {"dt": {"$gte": since}}}},
        {"segment_history": 1}).batch_size(500)
    import re as _re
    expired_re = _re.compile("expired|churn", _re.IGNORECASE)
    async for doc in cursor:
        history = doc.get("segment_history") or []
        for idx, entry in enumerate(history):
            if not isinstance(entry, dict):
                continue
            dt = as_utc(entry.get("dt"))
            segment = str(entry.get("segment") or "")
            prev = str(history[idx - 1].get("segment") or "") if idx else ""
            if (dt and dt >= since and expired_re.search(segment)
                    and not expired_re.search(prev)):
                churned += 1
                break  # count a user once

    value30 = horizons["d30"]["value_per_reg"]
    churn_lost_monthly = r2(churned * monthly_cost)
    # steady state: R regs/day → 30R regs a month → 30R × value30 per month
    regs_to_offset_churn = (round(churn_lost_monthly / (30 * value30), 1)
                            if value30 > 0 else None)

    return {
        "horizons": horizons,
        "trend": trend,
        "regs_per_day_14d": regs_per_day,
        "current_monthly_value": r2(regs_per_day * 30 * value30),
        "churned_30d": churned,
        "churn_lost_monthly_rub": churn_lost_monthly,
        "regs_per_day_to_offset_churn": regs_to_offset_churn,
        "monthly_sub_cost": r2(monthly_cost),
    }


@router.get("/registration-economics")
async def registration_economics() -> dict[str, Any]:
    """Ценность одной регистрации и сколько регистраций в день нужно."""
    return await _registration_economics()
