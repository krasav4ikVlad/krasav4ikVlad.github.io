"""Promo, campaign and broadcast analytics.

Promo endpoints aggregate ``kind=promo`` credits in ``transactions_flat``
(grouped by ``promo_code``, with per-user repeat counts to surface abuse).
Campaign attribution folds the raw ``users_flat.campaigns`` field in Python —
legacy data stores it either as a single dict or a list of dicts with
``converted_from`` / ``converted_amount``. Broadcast impact compares topups in
the N hours after each broadcast against the same window before it; the
``broadcasts`` collection is optional and the endpoint degrades gracefully.
Cached helpers accept only JSON-serializable kwargs per the caching contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..agg import NET_AMOUNT, as_utc, iso, r2
from ..cache import cached
from ..db import TX_FLAT, USERS_FLAT, get_db
from ..normalizer import parse_amount, parse_dt
from .deps import Period, get_period

router = APIRouter(prefix="/promos", tags=["promos"])

_ABUSE_LIMIT = 100
_BROADCASTS_LATEST = 20
_BROADCASTS_SCAN = 500  # raw docs scanned to find the 20 latest by parsed dt
_BROADCAST_NAME_LEN = 60


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _period_from_iso(from_iso: Optional[str], to_iso: Optional[str]) -> Period:
    """Rebuild a ``Period`` inside cached helpers (kwargs must be JSON-safe)."""
    from_dt = datetime.fromisoformat(from_iso) if from_iso else None
    to_dt = (datetime.fromisoformat(to_iso) if to_iso
             else datetime.now(timezone.utc))
    return Period(as_utc(from_dt), as_utc(to_dt) or datetime.now(timezone.utc))


def _promo_match(period: Period) -> dict:
    """$match for promo credits with a real code and a real dt in the period."""
    dt_cond: dict[str, Any] = dict(period.match()["dt"])
    dt_cond["$type"] = "date"
    return {"direction": "credit", "kind": "promo",
            "promo_code": {"$type": "string"}, "dt": dt_cond}


def _dt_iso(value: Any) -> Optional[str]:
    return iso(as_utc(value)) if isinstance(value, datetime) else None


# ---------------------------------------------------------------------------
# GET /promos/table
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="promos:table")
async def _table(*, from_iso: Optional[str], to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    rows = await db[TX_FLAT].aggregate([
        {"$match": _promo_match(period)},
        # per (code, user) first, so repeat counts are visible
        {"$group": {"_id": {"code": "$promo_code", "user": "$user_id"},
                    "count": {"$sum": 1},
                    "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "last_at": {"$max": "$dt"}}},
        {"$group": {"_id": "$_id.code",
                    "activations": {"$sum": "$count"},
                    "unique_users": {"$sum": 1},
                    "amount_total": {"$sum": "$amount"},
                    "max_by_one_user": {"$max": "$count"},
                    "abuse_suspects": {
                        "$sum": {"$cond": [{"$gte": ["$count", 2]}, 1, 0]}},
                    "last_used_at": {"$max": "$last_at"}}},
        {"$sort": {"activations": -1, "_id": 1}},
    ]).to_list(length=None)

    promos: list[dict[str, Any]] = []
    for row in rows:
        activations = int(row.get("activations") or 0)
        unique_users = int(row.get("unique_users") or 0)
        promos.append({
            "code": row.get("_id"),
            "activations": activations,
            "unique_users": unique_users,
            "amount_total": r2(row.get("amount_total")),
            "repeat_activations": max(activations - unique_users, 0),
            "max_by_one_user": int(row.get("max_by_one_user") or 0),
            "abuse_suspects": int(row.get("abuse_suspects") or 0),
            "last_used_at": _dt_iso(row.get("last_used_at")),
        })
    return {"promos": promos}


@router.get("/table")
async def table(period: Period = Depends(get_period)) -> dict:
    """Per-code promo stats: activations, unique users, repeats, abuse."""
    return await _table(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /promos/abuse
# ---------------------------------------------------------------------------

@cached(ttl=120, prefix="promos:abuse")
async def _abuse(*, from_iso: Optional[str], to_iso: Optional[str]) -> dict[str, Any]:
    db = get_db()
    period = _period_from_iso(from_iso, to_iso)
    rows = await db[TX_FLAT].aggregate([
        {"$match": _promo_match(period)},
        {"$group": {"_id": {"user": "$user_id", "code": "$promo_code"},
                    "count": {"$sum": 1},
                    "amount_total": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "first_at": {"$min": "$dt"},
                    "last_at": {"$max": "$dt"},
                    "username": {"$max": "$username"}}},
        {"$match": {"count": {"$gte": 2}}},
        {"$sort": {"count": -1, "amount_total": -1}},
        {"$limit": _ABUSE_LIMIT},
    ]).to_list(length=None)

    cases: list[dict[str, Any]] = []
    for row in rows:
        key = row.get("_id") or {}
        cases.append({
            "user_id": key.get("user"),
            "username": row.get("username"),
            "code": key.get("code"),
            "count": int(row.get("count") or 0),
            "amount_total": r2(row.get("amount_total")),
            "first_at": _dt_iso(row.get("first_at")),
            "last_at": _dt_iso(row.get("last_at")),
        })
    return {"cases": cases}


@router.get("/abuse")
async def abuse(period: Period = Depends(get_period)) -> dict:
    """Same user activating the same code twice or more."""
    return await _abuse(from_iso=iso(period.from_dt), to_iso=iso(period.to_dt))


# ---------------------------------------------------------------------------
# GET /promos/campaigns
# ---------------------------------------------------------------------------

def _campaign_entries(raw: Any) -> list[dict]:
    """Normalize the raw ``campaigns`` field into a list of dict entries."""
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


@cached(ttl=300, prefix="promos:campaigns")
async def _campaigns() -> dict[str, Any]:
    db = get_db()
    # stats keyed by campaign name: users / converted / converted_amount
    stats: dict[str, dict[str, float]] = {}

    cursor = db[USERS_FLAT].find(
        {"campaigns": {"$ne": None}},
        {"campaigns": 1, "topup_total": 1},
    )
    async for doc in cursor:
        # fold this user's entries per campaign (a user counts once even if
        # the raw field repeats the same campaign several times)
        per_campaign: dict[str, float] = {}
        for entry in _campaign_entries(doc.get("campaigns")):
            name = entry.get("converted_from")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            amount = parse_amount(entry.get("converted_amount")) or 0.0
            per_campaign[name] = per_campaign.get(name, 0.0) + amount

        topup_total = parse_amount(doc.get("topup_total")) or 0.0
        for name, amount in per_campaign.items():
            agg = stats.setdefault(
                name, {"users": 0, "converted": 0, "converted_amount": 0.0})
            agg["users"] += 1
            if amount > 0 or topup_total > 0:
                agg["converted"] += 1
            agg["converted_amount"] += amount

    campaigns = [
        {
            "campaign": name,
            "users": int(agg["users"]),
            "converted": int(agg["converted"]),
            "conversion_pct": (r2(agg["converted"] / agg["users"] * 100)
                               if agg["users"] else 0.0),
            "converted_amount": r2(agg["converted_amount"]),
        }
        for name, agg in stats.items()
    ]
    campaigns.sort(key=lambda c: (-c["users"], c["campaign"]))
    return {"campaigns": campaigns}


@router.get("/campaigns")
async def campaigns() -> dict:
    """Acquisition campaigns folded from ``users_flat.campaigns``."""
    return await _campaigns()


# ---------------------------------------------------------------------------
# GET /promos/broadcast-impact
# ---------------------------------------------------------------------------

def _broadcast_name(doc: dict) -> str:
    for key in ("name", "title", "text"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_BROADCAST_NAME_LEN]
    return "broadcast"


@cached(ttl=300, prefix="promos:broadcast-impact")
async def _broadcast_impact(*, hours: int) -> dict[str, Any]:
    db = get_db()
    if "broadcasts" not in await db.list_collection_names():
        return {"available": False, "broadcasts": []}

    # dt field naming varies across legacy docs — parse in Python; _id desc
    # is a decent insertion-order proxy to bound the scan.
    raw_docs = await db["broadcasts"].find({}).sort("_id", -1) \
        .limit(_BROADCASTS_SCAN).to_list(length=_BROADCASTS_SCAN)
    dated: list[tuple[datetime, dict]] = []
    for doc in raw_docs:
        dt = parse_dt(doc.get("dt") or doc.get("created_at") or doc.get("date"))
        if dt is not None:
            dated.append((as_utc(dt), doc))
    dated.sort(key=lambda item: item[0], reverse=True)

    window = timedelta(hours=hours)
    broadcasts: list[dict[str, Any]] = []
    for at, doc in dated[:_BROADCASTS_LATEST]:
        rows = await db[TX_FLAT].aggregate([
            {"$match": {"direction": "credit", "kind": "topup",
                        "dt": {"$type": "date", "$gte": at - window,
                               "$lt": at + window}}},
            {"$group": {
                "_id": {"$cond": [{"$gte": ["$dt", at]}, "after", "before"]},
                "count": {"$sum": 1},
                "revenue": {"$sum": NET_AMOUNT}}},
        ]).to_list(length=None)
        halves = {row["_id"]: row for row in rows}
        after, before = halves.get("after", {}), halves.get("before", {})
        revenue_after = r2(after.get("revenue"))
        revenue_before = r2(before.get("revenue"))
        broadcasts.append({
            "name": _broadcast_name(doc),
            "at": iso(at),
            "topups_after": int(after.get("count") or 0),
            "revenue_after": revenue_after,
            "topups_before": int(before.get("count") or 0),
            "revenue_before": revenue_before,
            "uplift_pct": (r2((revenue_after - revenue_before)
                              / revenue_before * 100)
                           if revenue_before else None),
        })
    return {"available": True, "broadcasts": broadcasts}


@router.get("/broadcast-impact")
async def broadcast_impact(hours: int = Query(24, ge=1, le=168)) -> dict:
    """Topups & net revenue N hours after each broadcast vs the window before."""
    return await _broadcast_impact(hours=hours)
