"""Уведомления для операторов: оценки пользователей по тикетам.

Оценка приписывается оператору, который вёл ЗАКРЫТУЮ сессию диалога:
идём от оценки назад по истории, находим ближайшее закрытие и берём
последнего оператора, ответившего до него (кнопки оценки уходят вместе
с закрытием, поэтому «свежий» оператор новой сессии оценку не получает).
TG-логины операторов приводятся к логинам панели через tg_username.

Оператор видит только свои оценки, владелец — все.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query

from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator
from ..utils import jsonable, utcnow
from .stats import CLOSE_RE, RATING_RE

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


async def _alias_map() -> dict[str, str]:
    """tg_username (нижний регистр) -> логин панели."""
    alias: dict[str, str] = {}
    async for o in get_db()[get_settings().operators_collection].find(
            {}, {"login": 1, "tg_username": 1}):
        alias[o["login"].lower()] = o["login"]
        tg = (o.get("tg_username") or "").strip().lstrip("@").lower()
        if tg:
            alias[tg] = o["login"]
    return alias


def _canon(login: str | None, alias: dict[str, str]) -> str | None:
    if not login:
        return None
    return alias.get(login.lower(), login)


async def _attribute_rating(msgs, uid: int, ts, alias: dict[str, str]) -> str | None:
    """Кому принадлежит оценка: последний оператор до ближайшего закрытия."""
    tail = await msgs.find(
        {"user_id": uid, "timestamp": {"$lt": ts}},
        {"direction": 1, "operator_login": 1, "text": 1},
    ).sort("timestamp", -1).limit(50).to_list(50)
    seen_close = False
    closer: str | None = None
    fallback: str | None = None
    for m in tail:  # от новых к старым
        d = m.get("direction")
        if d == "operator" and m.get("operator_login"):
            login = _canon(m["operator_login"], alias)
            if seen_close:
                return login  # оператор закрытой сессии — адресат оценки
            if fallback is None:
                fallback = login
        elif d == "system":
            text = m.get("text") or ""
            if RATING_RE.search(text):
                continue
            if CLOSE_RE.search(text):
                if not seen_close and m.get("operator_login"):
                    closer = _canon(m["operator_login"], alias)
                seen_close = True
    return fallback or closer


@router.get("/ratings")
async def rating_notifications(
    operator: CurrentOperator,
    days: float = Query(default=7, ge=1, le=31),
    limit: int = Query(default=30, ge=1, le=100),
):
    settings = get_settings()
    db = get_db()
    msgs = db[settings.support_messages_collection]
    alias = await _alias_map()
    since = utcnow() - timedelta(days=days)

    raw = [m async for m in msgs.find(
        {"direction": "system", "timestamp": {"$gte": since},
         "text": {"$regex": "Оценка"}},
        {"user_id": 1, "text": 1, "timestamp": 1},
    ).sort("timestamp", -1).limit(limit * 3)]

    items = []
    for m in raw:
        rating = RATING_RE.search(m.get("text") or "")
        uid = m.get("user_id")
        if not rating or uid is None:
            continue
        login = await _attribute_rating(msgs, uid, m.get("timestamp"), alias)
        if operator.get("role") != "owner" and login != operator["login"]:
            continue  # оператору — только свои оценки
        items.append({
            "user_id": uid,
            "stars": int(rating.group(1)),
            "operator_login": login,
            "timestamp": jsonable(m.get("timestamp")),
        })
        if len(items) >= limit:
            break

    # имена пользователей — одним запросом
    uids = list({i["user_id"] for i in items})
    names: dict = {}
    if uids:
        async for u in db[settings.users_collection].find(
                {"user_data.user_id": {"$in": uids}},
                {"user_data.user_id": 1, "user_data.username": 1,
                 "user_data.first_name": 1}):
            ud = u.get("user_data") or {}
            names[ud.get("user_id")] = {
                "username": ud.get("username"),
                "first_name": ud.get("first_name"),
            }
    for i in items:
        i.update(names.get(i["user_id"], {}))
    return {"items": items}
