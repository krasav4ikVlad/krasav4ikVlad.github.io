"""Проверка качества работы операторов («Проверка» / «Разборы»).

Владелец выбирает оператора, смотрит тикеты, где тот отвечал, и выносит
вердикт: «решено правильно» или «есть ошибки» (с конкретным описанием).
Оператор с неотработанными ошибками при входе в панель обязан сначала
пройти «работу над ошибками» — прочитать разборы и подтвердить каждый.

Вердикты лежат в отдельной коллекции панели (ticket_reviews), один вердикт
на пару (оператор, тикет) — повторная проверка перезаписывает старый; если
вердикт снова «ошибки», подтверждение сбрасывается и оператор проходит
разбор заново.
"""
from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query, status

from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator, OwnerOperator
from ..user_service import users_col
from ..utils import jsonable, utcnow

router = APIRouter(prefix="/api/reviews", tags=["reviews"])

MAX_TICKET_GROUPS = 5000  # потолок тикетов одного оператора в выборке


def _col():
    return get_db()[get_settings().reviews_collection]


def _messages_col():
    return get_db()[get_settings().support_messages_collection]


def _norm_tag(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


async def _operator_identities(login: str) -> list[str]:
    """Все написания логина оператора в сообщениях: логин панели + TG-теги."""
    op = await get_db()[get_settings().operators_collection].find_one(
        {"login": login},
        {"login": 1, "tg_username": 1, "tg_usernames": 1})
    if op is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")
    ids = {op["login"].lower()}
    legacy = [op.get("tg_username")] if op.get("tg_username") else []
    for raw in (op.get("tg_usernames") or []) + legacy:
        tag = _norm_tag(raw)
        if tag:
            ids.add(tag)
    return sorted(ids)


def _review_public(r: dict) -> dict:
    return {
        "id": str(r["_id"]),
        "operator_login": r.get("operator_login"),
        "user_id": r.get("user_id"),
        "verdict": r.get("verdict"),
        "mistakes": r.get("mistakes") or "",
        "reviewed_by": r.get("reviewed_by"),
        "created_at": jsonable(r.get("created_at")),
        "acked": bool(r.get("acked")),
        "acked_at": jsonable(r.get("acked_at")),
    }


# ---------------------------------------------------------------- владелец

@router.get("/tickets")
async def operator_tickets(
    owner: OwnerOperator,
    operator: str = Query(min_length=1, max_length=64),
    review_filter: str = Query(default="all", alias="filter", max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    """Тикеты, в которых оператор отвечал (по логину и всем его TG-тегам)."""
    if review_filter not in ("all", "unreviewed", "reviewed"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный фильтр")
    identities = await _operator_identities(operator)

    # какие написания логина реально встречаются в сообщениях — точечный $in
    # вместо вычислений по каждому документу (и работает по индексу)
    raw_logins = await _messages_col().distinct(
        "operator_login", {"direction": "operator"})
    mine = [r for r in raw_logins if r and _norm_tag(r) in identities]
    if not mine:
        return {"total": 0, "page": 1, "page_size": page_size, "items": []}

    groups = [g async for g in _messages_col().aggregate([
        {"$match": {"direction": "operator", "operator_login": {"$in": mine}}},
        {"$group": {"_id": "$user_id",
                    "replies": {"$sum": 1},
                    "last_reply_at": {"$max": "$timestamp"},
                    "first_reply_at": {"$min": "$timestamp"}}},
        {"$sort": {"last_reply_at": -1}},
        {"$limit": MAX_TICKET_GROUPS},
    ])]
    groups = [g for g in groups if g["_id"] is not None]

    reviews = {r["user_id"]: r async for r in _col().find({"operator_login": operator})}
    if review_filter == "unreviewed":
        groups = [g for g in groups if g["_id"] not in reviews]
    elif review_filter == "reviewed":
        groups = [g for g in groups if g["_id"] in reviews]

    total = len(groups)
    page_groups = groups[(page - 1) * page_size: page * page_size]

    # имена пользователей и статусы тикетов — одним запросом
    uids = [g["_id"] for g in page_groups]
    users: dict = {}
    if uids:
        async for u in users_col().find(
                {"user_data.user_id": {"$in": uids}},
                {"user_data.user_id": 1, "user_data.username": 1,
                 "user_data.first_name": 1, "info.support.status": 1}):
            ud = u.get("user_data") or {}
            support = ((u.get("info") or {}).get("support")) or {}
            users[ud.get("user_id")] = {
                "username": ud.get("username"),
                "first_name": ud.get("first_name"),
                "ticket_status": support.get("status"),
            }

    items = []
    for g in page_groups:
        uid = g["_id"]
        r = reviews.get(uid)
        items.append({
            "user_id": uid,
            "replies": g["replies"],
            "last_reply_at": jsonable(g.get("last_reply_at")),
            "first_reply_at": jsonable(g.get("first_reply_at")),
            "review": _review_public(r) if r else None,
            **users.get(uid, {"username": None, "first_name": None,
                              "ticket_status": None}),
        })
    return {"total": total, "page": page, "page_size": page_size,
            "items": items, "identities": identities}


@router.post("")
async def save_review(body: dict, owner: OwnerOperator):
    """Вердикт по тикету. verdict=ok — решено правильно; verdict=bad — есть
    ошибки (текст обязателен), оператору назначается работа над ошибками."""
    try:
        user_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный user_id")
    operator_login = str(body.get("operator_login") or "").strip().lower()
    verdict = body.get("verdict")
    mistakes = str(body.get("mistakes") or "").strip()
    if not operator_login or len(operator_login) > 64:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не указан оператор")
    if verdict not in ("ok", "bad"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Вердикт должен быть ok или bad")
    if verdict == "bad" and len(mistakes) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Опишите конкретные ошибки (минимум 3 символа)")
    if len(mistakes) > 4000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Слишком длинное описание (до 4000 символов)")
    op = await get_db()[get_settings().operators_collection].find_one(
        {"login": operator_login}, {"login": 1})
    if op is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")

    now = utcnow()
    await _col().update_one(
        {"operator_login": operator_login, "user_id": user_id},
        {"$set": {
            "verdict": verdict,
            "mistakes": mistakes if verdict == "bad" else "",
            "reviewed_by": owner["login"],
            "created_at": now,
            # «правильно» подтверждать нечего; «ошибки» требуют подтверждения
            # заново, даже если старый разбор уже был отработан
            "acked": verdict == "ok",
            "acked_at": None,
        }},
        upsert=True,
    )
    saved = await _col().find_one({"operator_login": operator_login, "user_id": user_id})
    return _review_public(saved)


# ---------------------------------------------------------------- оператор

@router.get("/my")
async def my_reviews(operator: CurrentOperator,
                     limit: int = Query(default=100, ge=1, le=300)):
    """Разборы текущего оператора. pending — сколько ошибок ещё не отработано
    (при pending > 0 фронт не пускает оператора дальше «Работы над ошибками»)."""
    rows = await _col().find({"operator_login": operator["login"]}) \
        .sort("created_at", -1).limit(limit).to_list(limit)
    items = [_review_public(r) for r in rows]
    pending = sum(1 for i in items if i["verdict"] == "bad" and not i["acked"])

    uids = list({i["user_id"] for i in items})
    names: dict = {}
    if uids:
        async for u in users_col().find(
                {"user_data.user_id": {"$in": uids}},
                {"user_data.user_id": 1, "user_data.username": 1,
                 "user_data.first_name": 1}):
            ud = u.get("user_data") or {}
            names[ud.get("user_id")] = {"username": ud.get("username"),
                                        "first_name": ud.get("first_name")}
    for i in items:
        i.update(names.get(i["user_id"], {"username": None, "first_name": None}))
    return {"items": items, "pending": pending}


@router.post("/{review_id}/ack")
async def ack_review(review_id: str, operator: CurrentOperator):
    """Оператор подтверждает, что прочитал разбор и учтёт ошибки."""
    try:
        oid = ObjectId(review_id)
    except InvalidId:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный id")
    r = await _col().find_one({"_id": oid})
    if r is None or r.get("operator_login") != operator["login"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Разбор не найден")
    if not r.get("acked"):
        await _col().update_one({"_id": oid},
                                {"$set": {"acked": True, "acked_at": utcnow()}})
    pending = await _col().count_documents(
        {"operator_login": operator["login"], "verdict": "bad", "acked": False})
    return {"ok": True, "pending": pending}
