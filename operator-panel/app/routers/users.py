"""Read-only endpoints: search, user card, histories (logs / transactions / bypass / referrals)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query

from ..security import CurrentOperator
from ..user_service import brief_view, find_user_or_404, search_users
from ..utils import jsonable, parse_any_ts

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=128), _op: CurrentOperator = None):
    docs = await search_users(q)
    return {"results": [brief_view(d) for d in docs], "count": len(docs)}


@router.get("/{user_id}")
async def user_card(user_id: int, _op: CurrentOperator = None):
    doc = await find_user_or_404(user_id, projection={
        "user_data": 1, "info.balance": 1, "info.email": 1,
        "info.ref_stats.withdrawable": 1, "info.ref_stats.earned_total": 1,
        "vpn": 1, "growth": 1, "role": 1,
    })
    ud = doc.get("user_data") or {}
    info = doc.get("info") or {}
    vpn = doc.get("vpn") or {}
    ref = info.get("ref_stats") or {}
    return jsonable({
        "user_data": ud,
        "balance": info.get("balance", 0),
        "email": info.get("email"),
        "ref_withdrawable": ref.get("withdrawable", 0),
        "ref_earned_total": ref.get("earned_total", 0),
        "vpn": {
            "period": vpn.get("period"),
            "uuid": vpn.get("uuid"),
            "shortUuid": vpn.get("shortUuid"),
            "createdAt": vpn.get("createdAt"),
            "expireAt": vpn.get("expireAt"),
            "hwidDeviceLimit": vpn.get("hwidDeviceLimit"),
            "extraDevices": vpn.get("extraDevices", []),
            "bypass_expireAt": vpn.get("bypass_expireAt"),
            "bypass_trafficLimitBytes": vpn.get("bypass_trafficLimitBytes"),
            "activeInternalSquads": vpn.get("activeInternalSquads", []),
        },
        "growth": doc.get("growth") or {},
        "role": doc.get("role"),
    })


def _filter_entries(
    entries: list[dict],
    *,
    search: str | None,
    date_from: str | None,
    date_to: str | None,
    text_fields: tuple[str, ...],
    ts_field: str = "timestamp",
) -> list[dict]:
    """In-memory filter over an embedded history array (arrays live inside one user doc,
    so filtering in Python is fine — we never scan the collection)."""
    dt_from = parse_any_ts(date_from)
    dt_to = parse_any_ts(date_to)
    if dt_to is not None and date_to and len(date_to.strip()) <= 10:
        dt_to = dt_to + timedelta(days=1)  # date without time — include the whole day
    needle = (search or "").strip().lower()

    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if needle:
            haystack = " ".join(str(e.get(f, "")) for f in text_fields).lower()
            if needle not in haystack:
                continue
        if dt_from or dt_to:
            ts = parse_any_ts(e.get(ts_field))
            if ts is None:
                continue
            if dt_from and ts < dt_from:
                continue
            if dt_to and ts >= dt_to:
                continue
        out.append(e)
    return out


def _paginate(items: list, page: int, page_size: int) -> dict:
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": jsonable(items[start:start + page_size]),
    }


@router.get("/{user_id}/logs")
async def user_logs(
    user_id: int,
    search: str | None = Query(default=None, max_length=200),
    action_type: str | None = Query(default=None, max_length=200),
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _op: CurrentOperator = None,
):
    doc = await find_user_or_404(user_id, projection={"logs": 1})
    entries = list(reversed(doc.get("logs") or []))  # newest first
    if action_type:
        entries = [e for e in entries
                   if isinstance(e, dict) and action_type.lower() in str(e.get("action", "")).lower()]
    entries = _filter_entries(entries, search=search, date_from=date_from, date_to=date_to,
                              text_fields=("action", "details"))
    return _paginate(entries, page, page_size)


@router.get("/{user_id}/transactions")
async def user_transactions(
    user_id: int,
    search: str | None = Query(default=None, max_length=200),
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _op: CurrentOperator = None,
):
    doc = await find_user_or_404(user_id, projection={"info.transactions": 1, "info.logs_balance": 1})
    info = doc.get("info") or {}
    balance_log = _filter_entries(
        list(reversed(info.get("logs_balance") or [])),
        search=search, date_from=date_from, date_to=date_to,
        text_fields=("details", "amount"),
    )
    transactions = _filter_entries(
        list(reversed(info.get("transactions") or [])),
        search=search, date_from=date_from, date_to=date_to,
        text_fields=("details", "amount", "method", "status", "type", "id"),
    )
    return {
        "balance_log": _paginate(balance_log, page, page_size),
        "transactions": _paginate(transactions, page, page_size),
    }


@router.get("/{user_id}/bypass-purchases")
async def bypass_purchases(user_id: int, _op: CurrentOperator = None):
    doc = await find_user_or_404(user_id, projection={"info.bypass_stats": 1})
    purchases = ((doc.get("info") or {}).get("bypass_stats") or {}).get("purchases") or []
    return {"items": jsonable(list(reversed(purchases))), "total": len(purchases)}


@router.get("/{user_id}/referrals")
async def referrals(user_id: int, _op: CurrentOperator = None):
    doc = await find_user_or_404(user_id, projection={"info.ref_stats": 1, "user_data.referrer": 1})
    ref = (doc.get("info") or {}).get("ref_stats") or {}
    return jsonable({
        "referrer": (doc.get("user_data") or {}).get("referrer", ""),
        "withdrawable": ref.get("withdrawable", 0),
        "earned_total": ref.get("earned_total", 0),
        "referrals": ref.get("referrals", []),
        "methods": ref.get("method", []),
    })
