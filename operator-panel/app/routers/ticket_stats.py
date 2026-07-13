"""Аналитика тикетов для владельца.

Когда приходят обращения (тепловая карта день×час, динамика по дням),
кто пишет (типы пользователей по сегментам основного бота) и — через ИИ —
о чём пишут (темы вопросов с кратким описанием проблемы).

Обращение = сообщение пользователя, перед которым в диалоге не было
неотвеченного сообщения пользователя (начало новой цепочки: первое сообщение
диалога или сообщение после ответа оператора/закрытия). Та же логика, что
у «отвеченных обращений» в норме активности.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status

from .. import ai
from ..ai import AIError
from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator, OwnerOperator
from ..utils import utcnow
from .stats import _as_utc, _load_act_settings, _parse_date

router = APIRouter(prefix="/api/ticket-stats", tags=["ticket-stats"])

MAX_PERIOD_DAYS = 366
TOPICS_SAMPLE_MAX = 150  # снимок обращений, который читает ИИ

# фиксированный порядок типов — сортировка по объёму делается на фронте
USER_TYPES = (
    "Платящие, подписка активна",
    "Подписка активна, не платили",
    "Новички на триале",
    "Триал кончился, не платили",
    "Платили, подписка истекла",
    "Без подписки",
    "Не определён",
)


def _classify_user(doc: dict | None, now: datetime) -> str:
    """Тип пользователя: по growth.segment основного бота, с фолбэком по vpn."""
    if not doc:
        return "Не определён"
    seg = (((doc.get("growth") or {}).get("segment")) or "").lower()
    if seg.startswith("new_trial"):
        return "Новички на триале"
    if seg == "trial":
        return "Триал кончился, не платили"
    if seg == "active_no_topup":
        return "Подписка активна, не платили"
    if seg in ("first_payment_active", "active_paid", "expiring_3d"):
        return "Платящие, подписка активна"
    if seg.startswith("expired") or seg.startswith("churned"):
        return "Платили, подписка истекла"
    if seg == "inactive_no_sub":
        return "Без подписки"
    # сегмента нет — грубая оценка по подписке
    vpn = doc.get("vpn") or {}
    if not (vpn.get("shortUuid") or "").strip():
        return "Без подписки"
    exp = _as_utc(vpn.get("expireAt"))
    if exp is None:
        return "Не определён"
    return ("Подписка активна, не платили" if exp > now
            else "Платили, подписка истекла")


def _period(date_from: str, date_to: str) -> tuple[datetime, datetime]:
    start = _parse_date(date_from, "date_from")
    end0 = _parse_date(date_to, "date_to")
    if end0 < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Дата «по» раньше даты «с»")
    if (end0 - start).days > MAX_PERIOD_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Период больше {MAX_PERIOD_DAYS} дней")
    return start, end0 + timedelta(days=1)


async def _collect_chains(start: datetime, end: datetime,
                          with_text: bool = False) -> tuple[list[dict], int]:
    """Начала обращений за период + общее число сообщений."""
    settings = get_settings()
    msgs = get_db()[settings.support_messages_collection]
    proj = {"user_id": 1, "direction": 1, "timestamp": 1}
    if with_text:
        proj["text"] = 1
    cursor = msgs.find(
        {"timestamp": {"$gte": start, "$lt": end}}, proj,
    ).sort([("user_id", 1), ("timestamp", 1)])

    chains: list[dict] = []
    msg_count = 0
    cur_uid = None
    waiting = False  # в диалоге висит неотвеченное сообщение пользователя
    async for m in cursor:
        msg_count += 1
        uid = m.get("user_id")
        ts = _as_utc(m.get("timestamp"))
        if uid is None or ts is None:
            continue
        if uid != cur_uid:
            cur_uid, waiting = uid, False
        d = m.get("direction")
        if d == "user":
            if not waiting:
                item = {"uid": uid, "ts": ts}
                if with_text:
                    item["text"] = (m.get("text") or "").strip()
                chains.append(item)
            waiting = True
        else:  # operator или system (ответ/закрытие) — цепочка завершена
            waiting = False
    return chains, msg_count


@router.get("")
async def ticket_stats(
    _owner: OwnerOperator,
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
):
    start, end = _period(date_from, date_to)
    act = await _load_act_settings()
    tz = timezone(timedelta(hours=act.get("tz_offset_hours", 3)))

    chains, msg_count = await _collect_chains(start, end)

    heat = [[0] * 24 for _ in range(7)]  # Пн..Вс × 0..23 (локальное время)
    by_date: dict[str, int] = {}
    d = start
    while d < end:
        by_date[d.strftime("%Y-%m-%d")] = 0
        d += timedelta(days=1)
    for c in chains:
        lt = c["ts"].astimezone(tz)
        heat[lt.weekday()][lt.hour] += 1
        key = lt.strftime("%Y-%m-%d")
        if key in by_date:
            by_date[key] += 1

    peak = None
    for dow in range(7):
        for hour in range(24):
            if heat[dow][hour] and (peak is None or heat[dow][hour] > peak["count"]):
                peak = {"dow": dow, "hour": hour, "count": heat[dow][hour]}

    # --- типы пользователей ---
    appeals_by_uid = Counter(c["uid"] for c in chains)
    users_col = get_db()[get_settings().users_collection]
    now = utcnow()
    type_agg = {t: {"type": t, "users": 0, "appeals": 0} for t in USER_TYPES}
    uids = list(appeals_by_uid)
    found: dict = {}
    for i in range(0, len(uids), 1000):
        async for u in users_col.find(
                {"user_data.user_id": {"$in": uids[i:i + 1000]}},
                {"user_data.user_id": 1, "growth.segment": 1,
                 "vpn.shortUuid": 1, "vpn.expireAt": 1}):
            found[(u.get("user_data") or {}).get("user_id")] = u
    for uid, n in appeals_by_uid.items():
        t = _classify_user(found.get(uid), now)
        type_agg[t]["users"] += 1
        type_agg[t]["appeals"] += n

    return {
        "date_from": date_from, "date_to": date_to,
        "tz_offset_hours": act.get("tz_offset_hours", 3),
        "totals": {
            "appeals": len(chains),
            "messages": msg_count,
            "users": len(appeals_by_uid),
            "peak": peak,
        },
        "heatmap": heat,
        "by_date": [{"date": k, "count": v} for k, v in sorted(by_date.items())],
        "user_types": [t for t in type_agg.values() if t["appeals"] or t["users"]],
    }


# ---------------------------------------------------------------- сводка для страницы «Тикеты»

@router.get("/summary")
async def tickets_summary(_op: CurrentOperator):
    """Живая сводка для всех операторов: очередь по статусам и обращения за
    сегодня (локальные сутки) — сколько пришло, за последний час, отвечено ли."""
    settings = get_settings()
    db = get_db()
    users = db[settings.users_collection]
    msgs = db[settings.support_messages_collection]
    act = await _load_act_settings()
    tz = timezone(timedelta(hours=act.get("tz_offset_hours", 3)))
    now = utcnow()
    day_start = now.astimezone(tz).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    pending_cnt = await users.count_documents(
        {"info.support.status": "pending", "info.support.thread_id": {"$exists": True}})
    open_cnt = await users.count_documents(
        {"info.support.status": "open", "info.support.thread_id": {"$exists": True}})

    # сутки контекста до полуночи, чтобы продолжение вчерашней переписки
    # не считалось новым обращением
    cursor = msgs.find(
        {"timestamp": {"$gte": day_start - timedelta(days=1)}},
        {"user_id": 1, "direction": 1, "timestamp": 1},
    ).sort([("user_id", 1), ("timestamp", 1)])

    day_chains: list[dict] = []
    waiting_uids: set = set()  # диалоги, где прямо сейчас последнее слово за пользователем
    cur_uid = None
    waiting = False
    open_chain: dict | None = None
    async for m in cursor:
        uid = m.get("user_id")
        ts = _as_utc(m.get("timestamp"))
        if uid is None or ts is None:
            continue
        if uid != cur_uid:
            if cur_uid is not None and waiting:
                waiting_uids.add(cur_uid)
            cur_uid, waiting, open_chain = uid, False, None
        if m.get("direction") == "user":
            if not waiting:
                ch = {"ts": ts, "answered": False}
                if ts >= day_start:
                    day_chains.append(ch)
                open_chain = ch
            waiting = True
        else:  # ответ оператора или системное закрытие завершают цепочку
            if open_chain is not None:
                open_chain["answered"] = True
                open_chain = None
            waiting = False
    if cur_uid is not None and waiting:
        waiting_uids.add(cur_uid)

    waiting_now = 0
    if waiting_uids:
        waiting_now = await users.count_documents({
            "user_data.user_id": {"$in": list(waiting_uids)},
            "info.support.status": {"$in": ["pending", "open"]},
        })

    hour_ago = now - timedelta(hours=1)
    answered = sum(1 for c in day_chains if c["answered"])
    return {
        "pending": pending_cnt,
        "open": open_cnt,
        "waiting_now": waiting_now,
        "today": {
            "appeals": len(day_chains),
            "answered": answered,
            "unanswered": len(day_chains) - answered,
            "last_hour": sum(1 for c in day_chains if c["ts"] >= hour_ago),
        },
        "tz_offset_hours": act.get("tz_offset_hours", 3),
    }


# ---------------------------------------------------------------- темы (ИИ)

def _topics_cache():
    return get_db()["ticket_topics_cache"]


def _cache_key(date_from: str, date_to: str) -> str:
    return f"{date_from}|{date_to}"


@router.get("/topics")
async def topics_cached(
    _owner: OwnerOperator,
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
):
    """Кэшированный отчёт (если анализ уже делали) — без обращения к ИИ."""
    doc = await _topics_cache().find_one({"_id": _cache_key(date_from, date_to)})
    return {"cached": bool(doc), "report": (doc or {}).get("report")}


@router.post("/topics")
async def topics_analyze(
    _owner: OwnerOperator,
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
    force: bool = Query(default=False),
):
    key = _cache_key(date_from, date_to)
    if not force:
        doc = await _topics_cache().find_one({"_id": key})
        if doc:
            return {"cached": True, "report": doc["report"]}

    start, end = _period(date_from, date_to)
    chains, _ = await _collect_chains(start, end, with_text=True)
    texts = [" ".join(c["text"].split())[:160] for c in chains if len(c.get("text") or "") >= 3]
    if len(texts) < 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Слишком мало текстовых обращений за период (нужно хотя бы 5)")

    # равномерная выборка по периоду, чтобы не перекормить модель
    if len(texts) > TOPICS_SAMPLE_MAX:
        step = len(texts) / TOPICS_SAMPLE_MAX
        sample = [texts[int(i * step)] for i in range(TOPICS_SAMPLE_MAX)]
    else:
        sample = texts

    try:
        result = await ai.analyze_ticket_topics(sample)
    except AIError as e:
        raise HTTPException(e.status_code, e.message)

    topics = []
    for t in (result.get("topics") or []):
        try:
            count = max(0, int(t.get("count") or 0))
        except (TypeError, ValueError):
            count = 0
        examples = []
        for idx in (t.get("examples") or [])[:2]:
            try:
                examples.append(sample[int(idx) - 1][:120])
            except (TypeError, ValueError, IndexError):
                pass
        topics.append({
            "name": str(t.get("name") or "Без названия")[:80],
            "count": count,
            "share": round(count / len(sample) * 100, 1) if sample else 0,
            "summary": str(t.get("summary") or "")[:500],
            "examples": examples,
        })
    topics.sort(key=lambda t: t["count"], reverse=True)

    report = {
        "date_from": date_from, "date_to": date_to,
        "analyzed": len(sample), "total": len(texts),
        "topics": topics,
        "created_at": utcnow().isoformat(),
    }
    await _topics_cache().update_one(
        {"_id": key}, {"$set": {"report": report}}, upsert=True)
    return {"cached": False, "report": report}
