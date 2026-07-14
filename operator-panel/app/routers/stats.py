"""Активность операторов — метрики для мотивации и привязки к зарплате.

Всё считается из support_messages (общая с ботом коллекция), поэтому
учитываются и ответы с сайта, и ответы из Telegram-треда (если бот
логирует их с operator_login — см. docs/bot-integration.md):

  * ответы и число тикетов, в которых оператор участвовал;
  * закрытия тикетов (системные сообщения «Тикет закрыт…» с operator_login) —
    ТОЛЬКО справочно: тикеты закрываются автоматически (app/autoclose.py),
    поэтому баллов закрытие не даёт и в норму не входит;
  * скорость первого ответа: от первого НЕотвеченного сообщения пользователя
    до ответа оператора (паузы дольше суток не учитываются в скорости);
  * оценки пользователей (системные сообщения «Оценка: N» — приписываются
    оператору, который последним вёл тикет).

Баллы (формула фиксированная, показана операторам в интерфейсе):
  ответ +2 · быстрый ответ (≤10 мин) ещё +3
  оценка: +2×(N−3) → 5★=+4, 4★=+2, 3★=0, 2★=−2, 1★=−4

Зарплата по коэффициенту (настройки владельца — «Настройки расчёта»).
Режимы нормы:
  auto (по нагрузке, рекомендуется) — норма привязана к реальной работе
    периода (workload-based quota, стандарт контакт-центров):
      potential = отвеченные_обращения×(2+3) + очередь×(2+3)
    Отвеченное обращение — цепочка сообщений пользователя, получившая ответ
    оператора; очередь — только РЕАЛЬНО брошенные тикеты: статус pending,
    либо open, где оператор за период не написал ни разу. «Хвосты» вида
    «спасибо» после ответа в вечно-открытых тикетах и сообщения, обработанные
    ботом, очередью не считаются. Персональная норма = potential × (часы оператора
    / часы команды). Игнорируете тикеты — очередь растёт и тянет норму вверх
    с тем же весом, что отвеченное обращение; нет нагрузки — коэффициент 1.0.
  team_avg — средний темп команды (баллы команды / часы команды);
  manual — число баллов/час задаёт владелец.
Далее одинаково: норма_баллов = ставка × часы_оператора_за_период,
коэффициент = баллы/норма в пределах [мин; макс],
к выплате = оклад × коэффициент × дней/30.44.
Операторы с графиком, но без единого ответа за период, попадают в таблицу
с нулевыми баллами — простой виден, а не прячется.
Часы и оклад задаются в карточке оператора; деньги видит ТОЛЬКО владелец.

Рабочее окно поддержки (например 09:00–24:00 МСК): ожидание ответа
считается только внутри окна — ночь, когда никто не дежурит, не портит
скорость ответа (вопрос в 23:30, ответ в 09:05 = 5 минут, а не 9,5 часов).

Смотреть рейтинг могут все операторы (это и есть мотивация).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..autoclose import load_autoclose_settings
from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator, OwnerOperator

router = APIRouter(prefix="/api/stats", tags=["stats"])

# ---------------------------------------------------------------- настройки расчёта

DEFAULT_ACT = {
    "norm_mode": "auto",           # auto = по нагрузке; team_avg = темп команды; manual
    "norm_points_per_hour": 10.0,  # используется только при norm_mode=manual
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
    norm_mode: str = Field(default="manual", pattern="^(auto|team_avg|manual)$")
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


class EscalationSettings(BaseModel):
    enabled: bool
    minutes: float = Field(ge=5, le=1440)
    repeat_minutes: float = Field(ge=10, le=1440)


@router.get("/escalation")
async def get_escalation_settings(_op: CurrentOperator):
    from ..escalation import load_escalation_settings
    return await load_escalation_settings()


@router.put("/escalation")
async def put_escalation_settings(body: EscalationSettings, _owner: OwnerOperator):
    await get_db()["panel_settings"].update_one(
        {"_id": "escalation"}, {"$set": body.model_dump()}, upsert=True)
    from ..escalation import load_escalation_settings
    return await load_escalation_settings()


class AutocloseSettings(BaseModel):
    enabled: bool
    hours: float = Field(ge=1, le=720)  # от часа до 30 дней


@router.get("/autoclose")
async def get_autoclose_settings(_op: CurrentOperator):
    return await load_autoclose_settings()


@router.put("/autoclose")
async def put_autoclose_settings(body: AutocloseSettings, _owner: OwnerOperator):
    await get_db()["panel_settings"].update_one(
        {"_id": "autoclose"}, {"$set": body.model_dump()}, upsert=True)
    return await load_autoclose_settings()


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


DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHED_FIXED_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
SCHED_FLOAT_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})~(\d{1,2}(?:\.\d)?)$")


def _sched_cfg(schedule: dict | None) -> dict[int, tuple[int, int, float | None]] | None:
    """schedule -> {weekday: (start_min, end_min, grace_min|None)}.
    grace=None — фиксированный день; None вместо словаря — графика нет."""
    if not isinstance(schedule, dict):
        return None
    out: dict[int, tuple[int, int, float | None]] = {}
    for i, day in enumerate(DAY_ORDER):
        v = (schedule.get(day) or "").strip()
        if not v:
            continue
        fm = SCHED_FLOAT_RE.match(v)
        if fm:
            start = int(fm.group(1)) * 60 + int(fm.group(2))
            end = int(fm.group(3)) * 60 + int(fm.group(4)) or 1440
            out[i] = (start, end, float(fm.group(5)) * 60)
            continue
        m = SCHED_FIXED_RE.match(v)
        if m:
            start = int(m.group(1)) * 60 + int(m.group(2))
            end = int(m.group(3)) * 60 + int(m.group(4))
            out[i] = (start, end, None)
    return out


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

    # TG-username -> логин панели: ответы из Telegram-треда засчитываются
    # тому же оператору, а не отдельной строкой рейтинга
    alias: dict[str, str] = {}
    op_info: dict[str, dict] = {}
    async for o in get_db()[settings.operators_collection].find(
            {}, {"login": 1, "name": 1, "salary_base": 1, "hours_per_week": 1,
                 "schedule": 1, "tg_username": 1}):
        op_info[o["login"]] = o
        tg = (o.get("tg_username") or "").strip().lstrip("@").lower()
        if tg:
            alias[tg] = o["login"]

    def canon(raw_login: str) -> str:
        return alias.get((raw_login or "").strip().lstrip("@").lower(), raw_login)

    sched_cfgs = {lg: _sched_cfg(o.get("schedule")) for lg, o in op_info.items()}
    tzinfo = timezone(timedelta(hours=tz_off))

    col = get_db()[settings.support_messages_collection]

    # Пасс 1: первый ответ каждого оператора в каждый день — от него
    # отсчитывается рабочий день «по 1-му ответу»; последний ответ дня
    # нужен для «во сколько в среднем начинает/заканчивает» (владельцу)
    day_first: dict[tuple[str, object], datetime] = {}
    day_last: dict[tuple[str, object], datetime] = {}
    async for m in col.find(
            {"timestamp": {"$gte": start, "$lt": end}, "direction": "operator",
             "operator_login": {"$ne": None}},
            {"operator_login": 1, "timestamp": 1}):
        ts = _as_utc(m.get("timestamp"))
        if ts is None:
            continue
        key = (canon(m.get("operator_login")), ts.astimezone(tzinfo).date())
        if key not in day_first or ts < day_first[key]:
            day_first[key] = ts
        if key not in day_last or ts > day_last[key]:
            day_last[key] = ts

    def work_rhythm(login: str) -> dict | None:
        """Средние времена первого и последнего ответа по активным дням."""
        starts: list[int] = []
        ends: list[int] = []
        for (lg, d), ts in day_first.items():
            if lg != login:
                continue
            lt = ts.astimezone(tzinfo)
            starts.append(lt.hour * 60 + lt.minute)
            le = day_last[(lg, d)].astimezone(tzinfo)
            ends.append(le.hour * 60 + le.minute)
        if not starts:
            return None
        fmt = lambda m: f"{int(m) // 60:02d}:{int(m) % 60:02d}"
        return {"days": len(starts),
                "avg_start": fmt(sum(starts) / len(starts)),
                "avg_end": fmt(sum(ends) / len(ends))}

    def day_start(login: str, local_date) -> datetime | None:
        """Начало рабочего дня оператора «по 1-му ответу»: его первый ответ
        в этот день, но не раньше начала интервала и не позже начала+окно.
        None — если день не «плавающий» (фиксированный/выходной/нет графика)."""
        cfgs = sched_cfgs.get(login)
        cfg = cfgs.get(local_date.weekday()) if cfgs else None
        if not cfg or cfg[2] is None:
            return None
        start_dt = datetime(local_date.year, local_date.month, local_date.day,
                            tzinfo=tzinfo) + timedelta(minutes=cfg[0])
        latest = start_dt + timedelta(minutes=cfg[2])  # не позднее начала+окно
        first = day_first.get((login, local_date))
        if first is None:
            return latest
        return min(max(first, start_dt), latest)
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
    last_op_login: str | None = None       # последний ответивший оператор
    rating_owner: str | None = None        # чьей сессии достанутся оценки (снимок на закрытии)
    answered_waits = 0                     # обращения, на которые операторы ответили
    backlog_cand: dict = {}                # uid -> оператор участвовал в диалоге?
    dialog_had_op = False

    async for m in cursor:
        uid = m.get("user_id")
        if uid != current_uid:
            if pending_since is not None and current_uid is not None:
                backlog_cand[current_uid] = dialog_had_op  # кончился без ответа
            current_uid, pending_since, last_op_login = uid, None, None
            rating_owner = None
            dialog_had_op = False
        ts = _as_utc(m.get("timestamp"))
        direction = m.get("direction")
        login = m.get("operator_login")

        if direction == "user":
            if pending_since is None and ts is not None:
                pending_since = ts
        elif direction == "operator" and login:
            login = canon(login)
            b = bucket(login)
            b["replies"] += 1
            b["tickets"].add(uid)
            last_op_login = login
            dialog_had_op = True
            if pending_since is not None and ts is not None:
                answered_waits += 1
                raw = (ts - pending_since).total_seconds()
                if 0 <= raw <= 7 * 86400:  # брошенные на неделю тикеты не замеряем
                    eff_pending = pending_since
                    # день «по 1-му ответу»: до персонального старта дня
                    # ожидание не считается — рейтинг не портится
                    ds = day_start(login, ts.astimezone(tzinfo).date())
                    if ds is not None and ds > eff_pending:
                        eff_pending = ds
                    # ночь/нерабочее время поддержки тоже не считается
                    wait = _working_seconds(eff_pending, ts, ws_min, we_min, tz_off)
                    if wait <= MAX_MEASURED_WAIT_SEC:
                        b["waits"].append(wait)
                        if wait <= FAST_ANSWER_SEC:
                            b["fast"] += 1
            pending_since = None
        elif direction == "system":
            text = m.get("text") or ""
            rating = RATING_RE.search(text)
            if rating:
                # оценка относится к сессии, завершённой ПОСЛЕДНИМ закрытием
                # перед ней — а не к оператору, ответившему позже в новой
                # сессии того же диалога
                owner_login = rating_owner or last_op_login
                if owner_login:
                    bucket(owner_login)["ratings"].append(int(rating.group(1)))
            elif CLOSE_RE.search(text):
                closer = canon(login) if login else None
                if closer:
                    bucket(closer)["closes"] += 1
                rating_owner = last_op_login or closer
                pending_since = None
    if pending_since is not None and current_uid is not None:
        backlog_cand[current_uid] = dialog_had_op  # хвост последнего диалога

    # Очередь = только РЕАЛЬНО брошенные тикеты:
    #  * статус pending (никто не подключился), или
    #  * статус open, но оператор за период не написал НИ РАЗУ (игнор).
    # «Хвосты» вида «спасибо/ок» после ответа оператора в вечно-открытых
    # тикетах и сообщения, обработанные ботом, очередью НЕ считаются.
    backlog = 0
    if backlog_cand:
        users_col = get_db()[settings.users_collection]
        async for u in users_col.find(
                {"user_data.user_id": {"$in": list(backlog_cand)}},
                {"user_data.user_id": 1, "info.support.status": 1}):
            st = ((((u.get("info") or {}).get("support")) or {}).get("status") or "").lower()
            had_op = backlog_cand.get((u.get("user_data") or {}).get("user_id"), False)
            if st == "pending" or (st == "open" and not had_op):
                backlog += 1

    is_owner = op.get("role") == "owner"
    my_login = op.get("login")
    days = (end - start).days
    period_dates = [(start + timedelta(days=i)).date() for i in range(days)]

    def hours_in_period(login: str) -> float | None:
        """Часы оператора за период. С графиком — по календарю: фиксированные
        дни целиком, «плавающие» — от фактического старта (первый ответ, но не
        позже начала+окно) до конца дня. Без графика — часы/нед × дней/7."""
        cfgs = sched_cfgs.get(login)
        if cfgs is None:
            hw = (op_info.get(login) or {}).get("hours_per_week")
            return hw * days / 7 if hw else None
        total_min = 0.0
        for d in period_dates:
            cfg = cfgs.get(d.weekday())
            if not cfg:
                continue
            s_min, e_min, grace = cfg
            if grace is None:
                total_min += (e_min - s_min) if e_min > s_min else (1440 - s_min + e_min)
            else:
                ds = day_start(login, d)
                end_dt = datetime(d.year, d.month, d.day, tzinfo=tzinfo) + timedelta(minutes=e_min)
                total_min += max(0.0, (end_dt - ds).total_seconds() / 60)
        return total_min / 60 if total_min else None

    # операторы с часами в периоде, но без единого действия — в таблицу
    # с нулями: простой должен быть виден, а не прятаться
    for lg, o in op_info.items():
        if lg in per_op or not o.get("active", True) or o.get("role") == "owner":
            continue
        if hours_in_period(lg):
            bucket(lg)

    # фаза 1: метрики и часы каждого оператора
    rows = []
    for login, b in per_op.items():
        waits = sorted(b["waits"])
        avg_wait = sum(waits) / len(waits) if waits else None
        median_wait = waits[len(waits) // 2] if waits else None
        ratings = b["ratings"]
        # закрытия в баллы не входят: тикеты закрываются автоматически,
        # b["closes"] остаётся справочной колонкой
        score = (b["replies"] * POINTS_REPLY
                 + b["fast"] * POINTS_FAST
                 + sum((r - 3) * POINTS_PER_RATING_STEP for r in ratings))
        info = op_info.get(login, {})
        hours_period = hours_in_period(login)
        rows.append({
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
            "hours_per_week": info.get("hours_per_week"),
            "hours_period": round(hours_period, 1) if hours_period else None,
            "schedule": info.get("schedule"),
        })

    # фаза 2: ставка нормы (баллов/час)
    mode = act.get("norm_mode", "auto")
    # «доступные» баллы = реальная работа периода: отвеченные обращения и
    # висящая очередь (тот же вес — игнорировать тикеты невыгодно).
    # Закрытия в норму не входят: тикеты закрываются автоматически.
    potential = ((answered_waits + backlog)
                 * (POINTS_REPLY + POINTS_FAST))
    tot_hours = sum(r["hours_period"] for r in rows if r["hours_period"])
    if mode == "manual":
        rate = act["norm_points_per_hour"] or None
    elif mode == "team_avg":
        tot_score = sum(r["score"] for r in rows if r["hours_period"])
        rate = (tot_score / tot_hours) if tot_hours and tot_score > 0 else None
    else:  # auto: по нагрузке — норма не зависит от стараний команды
        rate = (potential / tot_hours) if tot_hours and potential > 0 else None

    clamp = lambda v: round(max(act["coeff_min"], min(act["coeff_max"], v)), 2)
    for r in rows:
        norm_points = coeff = None
        if rate and r["hours_period"]:
            norm_points = rate * r["hours_period"]
            coeff = clamp(r["score"] / norm_points)
        elif mode == "auto" and r["hours_period"] and potential == 0:
            coeff = clamp(1.0)  # нагрузки не было — простой не по вине оператора
        r["norm_points"] = round(norm_points) if norm_points else None
        r["coeff"] = coeff
        # оклад, выплата и рабочий ритм (когда начинает/заканчивает) — ТОЛЬКО владельцу
        if is_owner:
            r["work_rhythm"] = work_rhythm(r["login"])
            salary_base = (op_info.get(r["login"]) or {}).get("salary_base")
            r["salary_base"] = salary_base
            r["payout"] = (round(salary_base * coeff * days / 30.44)
                           if salary_base and coeff is not None else None)
    rows.sort(key=lambda r: r["score"], reverse=True)

    return {
        "date_from": date_from, "date_to": date_to, "days": days,
        "points": {"reply": POINTS_REPLY, "fast": POINTS_FAST,
                   "rating_step": POINTS_PER_RATING_STEP,
                   "fast_threshold_min": FAST_ANSWER_SEC // 60},
        "settings": act,
        "norm_used": round(rate, 2) if rate else None,
        "demand": {"answered": answered_waits,
                   "backlog": backlog, "potential_points": potential},
        "rows": rows,
    }
