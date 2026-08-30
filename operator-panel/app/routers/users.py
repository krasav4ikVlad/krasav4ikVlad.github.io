"""Read-only endpoints: search, user card, histories (balance_log / logs / transactions / bypass / referrals / payments)."""
from __future__ import annotations

import re

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status

from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator
from ..user_service import brief_view, find_user_or_404, search_users, users_col
from ..utils import jsonable, parse_any_ts

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=128), _op: CurrentOperator = None):
    docs = await search_users(q)
    return {"results": [brief_view(d) for d in docs], "count": len(docs)}


@router.get("/{user_id}")
async def user_card(user_id: int, _op: CurrentOperator = None):
    doc = await find_user_or_404(user_id, projection={
        "user_data": 1, "info.balance": 1, "info.email": 1, "info.is_banned": 1,
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
        "is_banned": bool(info.get("is_banned")),
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
            "bypass_hwidDeviceLimit": vpn.get("bypass_hwidDeviceLimit"),
            "activeInternalSquads": vpn.get("activeInternalSquads", []),
        },
        "growth": doc.get("growth") or {},
        "role": doc.get("role"),
    })


# ---------------------------------------------------------------- журнал денег (balance_log)
# Первоисточник движения денег с версии бота 104. Знак берём ТОЛЬКО из
# amount (+ приход / − расход) — на угадывании знака из описания старая
# панель показывала списания как пополнения.

BL_KINDS = ("topup", "plan", "renewal", "devices", "bypass", "private_server",
            "gift", "promo", "referral", "campaign", "survey", "payout",
            "refund", "admin", "other")


@router.get("/{user_id}/balance-log")
async def user_balance_log(
    user_id: int,
    kind: str | None = Query(default=None, max_length=32),
    account: str | None = Query(default=None, max_length=16),
    search: str | None = Query(default=None, max_length=200),
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _op: CurrentOperator = None,
):
    if kind and kind not in BL_KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный kind")
    if account and account not in ("balance", "referral"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный account")
    col = get_db()[get_settings().balance_log_collection]
    q: dict = {"user_id": user_id}
    if kind:
        q["kind"] = kind
    if account:
        q["account"] = account
    dt_from = parse_any_ts(date_from)
    dt_to = parse_any_ts(date_to)
    if dt_to is not None and date_to and len(date_to.strip()) <= 10:
        dt_to = dt_to + timedelta(days=1)  # дата без времени — включаем весь день
    if dt_from or dt_to:
        q["at"] = {}
        if dt_from:
            q["at"]["$gte"] = dt_from
        if dt_to:
            q["at"]["$lt"] = dt_to
    if search and search.strip():
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        q["$or"] = [{"title": rx}, {"description": rx}]

    total = await col.count_documents(q)
    items = [{k: jsonable(v) for k, v in d.items() if k != "_id"}
             async for d in col.find(q).sort("at", -1)
             .skip((page - 1) * page_size).limit(page_size)]

    # сверка из доки: sum(amount) по account=balance должна сходиться с
    # info.balance, если журнал вёлся с начала; расхождение = операции до v104
    journal_sum = None
    try:
        agg = await col.aggregate([
            {"$match": {"user_id": user_id, "account": "balance"}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount"}}},
        ]).to_list(1)
        if agg:
            journal_sum = agg[0].get("sum")
    except Exception:
        pass
    return {"total": total, "page": page, "page_size": page_size,
            "items": items, "journal_sum": journal_sum}


@router.get("/{user_id}/payments")
async def user_payments(
    user_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _op: CurrentOperator = None,
):
    """Платежи провайдеров — первоисточник выручки (реально уплаченное,
    без бонусов); в balance_log то же зачисление лежит уже с бонусом."""
    col = get_db()[get_settings().payments_collection]
    q = {"user_id": user_id}
    total = await col.count_documents(q)
    items = [{k: jsonable(v) for k, v in d.items() if k != "_id"}
             async for d in col.find(q).sort("created_at", -1)
             .skip((page - 1) * page_size).limit(page_size)]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


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


async def _sliced_page(user_id: int, array_field: str, page: int, page_size: int) -> dict | None:
    """Страница embedded-массива (новые сверху) без выкачивания всего массива:
    у активных пользователей logs/transactions — тысячи записей и мегабайты,
    а Mongo на другом сервере. Возвращает None, если у сервера/мока нет нужных
    операторов агрегации — тогда вызывающий код падает обратно на полную выборку."""
    end_excl_expr = {"$max": [0, {"$subtract": [
        {"$size": {"$ifNull": [f"${array_field}", []]}}, (page - 1) * page_size]}]}
    pipeline = [
        {"$match": {"user_data.user_id": user_id}},
        {"$project": {
            "_id": 0,
            "total": {"$size": {"$ifNull": [f"${array_field}", []]}},
            "items": {"$slice": [
                {"$ifNull": [f"${array_field}", []]},
                {"$max": [0, {"$subtract": [
                    {"$size": {"$ifNull": [f"${array_field}", []]}}, page * page_size]}]},
                {"$max": [1, {"$min": [page_size, end_excl_expr]}]},
            ]},
        }},
    ]
    try:
        docs = await users_col().aggregate(pipeline).to_list(1)
    except Exception:
        return None
    if not docs:
        return None
    doc = docs[0]
    total = doc.get("total", 0)
    # страница за пределами массива -> пустой список
    if (page - 1) * page_size >= total:
        items: list = []
    else:
        items = list(reversed(doc.get("items") or []))
    return {"total": total, "page": page, "page_size": page_size, "items": jsonable(items)}


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
    # Без фильтров отдаём страницу прямо из Mongo ($slice) — не гоняем весь массив.
    # None = юзер не найден ИЛИ агрегация недоступна -> обычный путь ниже (там и 404)
    if not any((search, action_type, date_from, date_to)):
        sliced = await _sliced_page(user_id, "logs", page, page_size)
        if sliced is not None:
            return sliced

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
    # Без фильтров — постраничные $slice-окна обоих массивов вместо полной выборки
    if not any((search, date_from, date_to)):
        import asyncio
        bl, tx = await asyncio.gather(
            _sliced_page(user_id, "info.logs_balance", page, page_size),
            _sliced_page(user_id, "info.transactions", page, page_size),
        )
        if bl is not None and tx is not None:
            return {"balance_log": bl, "transactions": tx}

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
