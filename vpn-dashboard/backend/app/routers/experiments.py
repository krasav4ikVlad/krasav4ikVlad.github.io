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

async def _median_debit(db: Any, kind: str, default: float) -> float:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    amounts = [r["amount"] async for r in db[TX_FLAT].find(
        {"direction": "debit", "kind": kind, "amount": {"$gt": 0},
         "dt": {"$type": "date", "$gte": since}},
        {"amount": 1}).limit(5000)]
    return float(statistics.median(amounts)) if amounts else default


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
    median_renewal = await _median_debit(db, "renewal", 199.0)
    median_check = await _median_check(db, 299.0)

    uf = db[USERS_FLAT]
    expiring_no_balance = await uf.count_documents({
        "days_to_expire": {"$gt": 0, "$lt": 3},
        "balance": {"$lt": median_renewal},
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
        "balance": {"$gte": median_renewal},
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
            "potential_rub": r2(expiring_no_balance * median_renewal * 0.4),
            "assumption": "конверсия напоминания 40%",
        },
        {
            "key": "winback_expired",
            "title": "Ушли за последние 7–60 дней (winback)",
            "description": "Платившие юзеры с истёкшей подпиской. Промокод на "
                           "возврат (например, −20% на месяц) окупается почти "
                           "всегда.",
            "users": winback,
            "potential_rub": r2(winback * median_renewal * 0.12),
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
            "potential_rub": r2(dormant_balance * median_renewal * 0.5),
            "assumption": "конверсия напоминания 50%",
        },
    ]
    opportunities.sort(key=lambda o: -o["potential_rub"])
    return {"median_renewal": r2(median_renewal),
            "median_check": r2(median_check),
            "opportunities": opportunities,
            "computed_at": iso(as_utc(now))}


@router.get("/opportunities")
async def opportunities() -> dict[str, Any]:
    return await _opportunities()
