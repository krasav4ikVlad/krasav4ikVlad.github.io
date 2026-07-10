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

Смотреть рейтинг могут все операторы (это и есть мотивация),
выгрузка CSV — у владельца на клиенте.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status

from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator

router = APIRouter(prefix="/api/stats", tags=["stats"])

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
    _op: CurrentOperator,
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
):
    start = _parse_date(date_from, "date_from")
    end = _parse_date(date_to, "date_to") + timedelta(days=1)  # включительно
    if end <= start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date_to раньше date_from")
    if (end - start).days > 366:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Период больше года")

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
                wait = (ts - pending_since).total_seconds()
                if 0 <= wait <= MAX_MEASURED_WAIT_SEC:
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

    # имена операторов — для красивой таблицы
    names: dict[str, str] = {}
    async for op in get_db()[settings.operators_collection].find({}, {"login": 1, "name": 1}):
        names[op["login"]] = op.get("name") or op["login"]

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
        rows.append({
            "login": login,
            "name": names.get(login, login),
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
        })
    rows.sort(key=lambda r: r["score"], reverse=True)

    return {
        "date_from": date_from, "date_to": date_to,
        "points": {"reply": POINTS_REPLY, "close": POINTS_CLOSE, "fast": POINTS_FAST,
                   "rating_step": POINTS_PER_RATING_STEP,
                   "fast_threshold_min": FAST_ANSWER_SEC // 60},
        "rows": rows,
    }
