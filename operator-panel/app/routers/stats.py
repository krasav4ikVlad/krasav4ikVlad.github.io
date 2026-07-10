"""Активность операторов — метрики для мотивации и привязки к зарплате.

Всё считается из support_messages (общая с ботом коллекция), поэтому
учитываются и ответы с сайта, и ответы из Telegram-треда (если бот
логирует их с operator_login — см. docs/bot-integration.md):

  * ответы и число тикетов, в которых оператор участвовал;
  * закрытия тикетов (системные сообщения «Тикет закрыт…» с operator_login);
  * скорость первого ответа: от первого НЕотвеченного сообщения пользователя
    до ответа оператора (паузы дольше суток не учитываются в скорости);
  * оценки пользователей (системные сообщения «Оценка: N» — приписываются
    оператору, который последним вёл тикет).

Баллы (формула фиксированная, показана операторам в интерфейсе):
  ответ +2 · закрытие +10 · быстрый ответ (≤10 мин) ещё +3
  оценка: +2×(N−3) → 5★=+4, 4★=+2, 3★=0, 2★=−2, 1★=−4

Зарплата по коэффициенту (настройки владельца — «Настройки расчёта»):
  норма баллов = норма_баллов_в_час × часы_оператора_за_период
  коэффициент  = баллы / норма, ограничен [коэфф_мин; коэфф_макс]
  к выплате    = оклад(₽/мес) × коэффициент × дней_периода / 30.44
Часы в неделю и оклад задаются владельцем в карточке оператора.

Рабочее окно поддержки (например 09:00–24:00 МСК): ожидание ответа
считается только внутри окна — ночь, когда никто не дежурит, не портит
скорость ответа (вопрос в 23:30, ответ в 09:05 = 5 минут, а не 9,5 часов).

Смотреть рейтинг могут все операторы (это и есть мотивация),
оклад и «к выплате» видит владелец и сам оператор — только свои.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator, OwnerOperator

router = APIRouter(prefix="/api/stats", tags=["stats"])

# ---------------------------------------------------------------- настройки расчёта

DEFAULT_ACT = {
    "norm_points_per_hour": 10.0,  # сколько баллов в час считается нормой
    "coeff_min": 0.0,
    "coeff_max": 1.5,
    "work_start": "09:00",         # окно работы поддержки (локальное время)
    "work_end": "24:00",           # 24:00 = до конца суток; start==end = круглосуточно
    "tz_offset_hours": 3,          # МСК
}

TIME_RE = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")


def _parse_hhmm(v: str, field: str) -> int:
    m = TIME_RE.match(v or "")
    if not m or (m.group(1) == "24" and m.group(2) != "00"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{field}: время в формате ЧЧ:ММ (например 09:00 или 24:00)")
    return int(m.group(1)) * 60 + int(m.group(2))


async def _load_act_settings() -> dict:
    doc = await get_db()["panel_settings"].find_one({"_id": "activity"}) or {}
    return {**DEFAULT_ACT, **{k: doc[k] for k in DEFAULT_ACT if k in doc}}


class ActivitySettings(BaseModel):
    norm_points_per_hour: float = Field(ge=0, le=10_000)
    coeff_min: float = Field(ge=0, le=10)
    coeff_max: float = Field(ge=0, le=10)
    work_start: str = Field(max_length=5)
    work_end: str = Field(max_length=5)
    tz_offset_hours: int = Field(ge=-12, le=14)


@router.get("/settings")
async def get_activity_settings(_op: CurrentOperator):
    """Правила расчёта видят все операторы — прозрачность мотивирует."""
    return await _load_act_settings()


@router.put("/settings")
async def put_activity_settings(body: ActivitySettings, _owner: OwnerOperator):
    _parse_hhmm(body.work_start, "work_start")
    _parse_hhmm(body.work_end, "work_end")
    if body.coeff_min > body.coeff_max:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Минимальный коэффициент больше максимального")
    await get_db()["panel_settings"].update_one(
        {"_id": "activity"}, {"$set": body.model_dump()}, upsert=True)
    return await _load_act_settings()


def _working_seconds(a: datetime, b: datetime, start_min: int, end_min: int,
                     tz_off: int) -> float:
    """Сколько секунд рабочего окна поддержки прошло между a и b.
    Окно может переходить через полночь (start > end, напр. 18:00–02:00)."""
    if b <= a:
        return 0.0
    if start_min == end_min:  # круглосуточно
        return (b - a).total_seconds()
    tz = timezone(timedelta(hours=tz_off))
    a_l, b_l = a.astimezone(tz), b.astimezone(tz)
    total = 0.0
    day = a_l.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    while day < b_l:
        if start_min < end_min:
            windows = [(day + timedelta(minutes=start_min), day + timedelta(minutes=end_min))]
        else:  # ночная смена через полночь
            windows = [(day + timedelta(minutes=start_min), day + timedelta(days=1, minutes=end_min))]
        for ws, we in windows:
            lo, hi = max(a_l, ws), min(b_l, we)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total

FAST_ANSWER_SEC = 10 * 60          # «быстрый ответ» — в течение 10 минут
MAX_MEASURED_WAIT_SEC = 24 * 3600  # ожидания дольше суток не портят среднюю скорость

POINTS_REPLY = 2
POINTS_CLOSE = 10
POINTS_FAST = 3
POINTS_PER_RATING_STEP = 2         # 2×(оценка−3)

RATING_RE = re.compile(r"Оценка[^\d]{0,20}([1-5])")
CLOSE_RE = re.compile(r"закрыт", re.IGNORECASE)


def _parse_date(v: str, field: str) -> datetime:
    try:
        return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Неверная дата {field} (нужен формат YYYY-MM-DD)")


def _as_utc(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@router.get("/operators")
async def operator_stats(
    op: CurrentOperator,
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
):
    start = _parse_date(date_from, "date_from")
    end = _parse_date(date_to, "date_to") + timedelta(days=1)  # включительно
    if end <= start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date_to раньше date_from")
    if (end - start).days > 366:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Период больше года")

    act = await _load_act_settings()
    ws_min = _parse_hhmm(act["work_start"], "work_start")
    we_min = _parse_hhmm(act["work_end"], "work_end")
    tz_off = act["tz_offset_hours"]

    settings = get_settings()
    col = get_db()[settings.support_messages_collection]
    cursor = col.find(
        {"timestamp": {"$gte": start, "$lt": end}},
        {"user_id": 1, "direction": 1, "operator_login": 1, "timestamp": 1, "text": 1},
    ).sort([("user_id", 1), ("timestamp", 1)])

    per_op: dict[str, dict] = {}

    def bucket(login: str) -> dict:
        return per_op.setdefault(login, {
            "replies": 0, "tickets": set(), "closes": 0,
            "waits": [], "fast": 0, "ratings": [],
        })

    current_uid = None
    pending_since: datetime | None = None  # первое неотвеченное сообщение пользователя
    last_op_login: str | None = None       # кому приписывать оценку

    async for m in cursor:
        uid = m.get("user_id")
        if uid != current_uid:
            current_uid, pending_since, last_op_login = uid, None, None
        ts = _as_utc(m.get("timestamp"))
        direction = m.get("direction")
        login = m.get("operator_login")

        if direction == "user":
            if pending_since is None and ts is not None:
                pending_since = ts
        elif direction == "operator" and login:
            b = bucket(login)
            b["replies"] += 1
            b["tickets"].add(uid)
            last_op_login = login
            if pending_since is not None and ts is not None:
                raw = (ts - pending_since).total_seconds()
                if 0 <= raw <= 7 * 86400:  # брошенные на неделю тикеты не замеряем
                    # ночь/нерабочее время не считается ожиданием
                    wait = _working_seconds(pending_since, ts, ws_min, we_min, tz_off)
                    if wait <= MAX_MEASURED_WAIT_SEC:
                        b["waits"].append(wait)
                        if wait <= FAST_ANSWER_SEC:
                            b["fast"] += 1
            pending_since = None
        elif direction == "system":
            text = m.get("text") or ""
            if login and CLOSE_RE.search(text):
                bucket(login)["closes"] += 1
                last_op_login = login
                pending_since = None
            else:
                rating = RATING_RE.search(text)
                if rating and last_op_login:
                    bucket(last_op_login)["ratings"].append(int(rating.group(1)))

    # данные операторов: имя + оклад + график
    op_info: dict[str, dict] = {}
    async for o in get_db()[settings.operators_collection].find(
            {}, {"login": 1, "name": 1, "salary_base": 1, "hours_per_week": 1, "schedule": 1}):
        op_info[o["login"]] = o

    is_owner = op.get("role") == "owner"
    my_login = op.get("login")
    days = (end - start).days

    rows = []
    for login, b in per_op.items():
        waits = sorted(b["waits"])
        avg_wait = sum(waits) / len(waits) if waits else None
        median_wait = waits[len(waits) // 2] if waits else None
        ratings = b["ratings"]
        score = (b["replies"] * POINTS_REPLY
                 + b["closes"] * POINTS_CLOSE
                 + b["fast"] * POINTS_FAST
                 + sum((r - 3) * POINTS_PER_RATING_STEP for r in ratings))

        info = op_info.get(login, {})
        hours_week = info.get("hours_per_week")
        salary_base = info.get("salary_base")
        # коэффициент: баллы против персональной нормы (норма_в_час × его часы за период)
        coeff = None
        norm_points = None
        if hours_week and act["norm_points_per_hour"] > 0:
            hours_period = hours_week * days / 7
            norm_points = act["norm_points_per_hour"] * hours_period
            if norm_points > 0:
                coeff = max(act["coeff_min"], min(act["coeff_max"], score / norm_points))
                coeff = round(coeff, 2)

        row = {
            "login": login,
            "name": info.get("name") or login,
            "replies": b["replies"],
            "tickets": len(b["tickets"]),
            "closes": b["closes"],
            "avg_wait_sec": round(avg_wait) if avg_wait is not None else None,
            "median_wait_sec": round(median_wait) if median_wait is not None else None,
            "fast": b["fast"],
            "measured": len(waits),
            "rating_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "rating_count": len(ratings),
            "score": score,
            "hours_per_week": hours_week,
            "schedule": info.get("schedule"),
            "norm_points": round(norm_points) if norm_points else None,
            "coeff": coeff,
        }
        # оклад и сумма к выплате — только владельцу и самому оператору
        if is_owner or login == my_login:
            row["salary_base"] = salary_base
            row["payout"] = (round(salary_base * coeff * days / 30.44)
                             if salary_base and coeff is not None else None)
        rows.append(row)
    rows.sort(key=lambda r: r["score"], reverse=True)

    return {
        "date_from": date_from, "date_to": date_to, "days": days,
        "points": {"reply": POINTS_REPLY, "close": POINTS_CLOSE, "fast": POINTS_FAST,
                   "rating_step": POINTS_PER_RATING_STEP,
                   "fast_threshold_min": FAST_ANSWER_SEC // 60},
        "settings": act,
        "rows": rows,
    }
