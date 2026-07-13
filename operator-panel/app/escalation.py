"""Эскалация неотвеченных обращений в Telegram.

Фоновая задача панели: раз в минуту ищет обращения, которые ждут ответа
дольше порога (настройка владельца: «Активность» → «Настройки расчёта» →
«Эскалация»), и шлёт алерт в общий раздел саппорт-чата со списком тикетов
и ссылками. Пока обращение не отвечено, алерт повторяется каждые
repeat_minutes; ответ оператора сбрасывает состояние.

Ожидание считается ВНУТРИ рабочего окна поддержки (как скорость ответа
в «Активности»): ночью, когда никто не дежурит, алерты не сыплются —
но утром, как только окно откроется, накопившиеся тикеты эскалируются.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .database import get_db
from .telegram import TelegramError, get_telegram
from .utils import utcnow

log = logging.getLogger(__name__)

DEFAULT_ESCALATION = {
    "enabled": True,
    "minutes": 15,         # без ответа дольше N минут (в рабочем окне) -> алерт
    "repeat_minutes": 30,  # повторять, пока не ответят
}

LOOKBACK_DAYS = 3  # обращения старше этого срока подчищает автозакрытие/уборка


async def load_escalation_settings() -> dict:
    doc = await get_db()["panel_settings"].find_one({"_id": "escalation"}) or {}
    return {**DEFAULT_ESCALATION,
            **{k: doc[k] for k in DEFAULT_ESCALATION if k in doc}}


def _as_utc(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _waiting_chains(msgs, since: datetime) -> dict[int, datetime]:
    """{uid: начало текущей НЕотвеченной цепочки} по диалогам с активностью."""
    cursor = msgs.find(
        {"timestamp": {"$gte": since}},
        {"user_id": 1, "direction": 1, "timestamp": 1},
    ).sort([("user_id", 1), ("timestamp", 1)])
    waiting: dict[int, datetime] = {}
    cur_uid = None
    chain_start: datetime | None = None
    async for m in cursor:
        uid = m.get("user_id")
        ts = _as_utc(m.get("timestamp"))
        if uid is None or ts is None:
            continue
        if uid != cur_uid:
            if cur_uid is not None and chain_start is not None:
                waiting[cur_uid] = chain_start
            cur_uid, chain_start = uid, None
        if m.get("direction") == "user":
            if chain_start is None:
                chain_start = ts
        else:
            chain_start = None
    if cur_uid is not None and chain_start is not None:
        waiting[cur_uid] = chain_start
    return waiting


async def escalation_pass() -> dict:
    """Один проход. Возвращает статистику (для логов и тестов)."""
    cfg = await load_escalation_settings()
    if not cfg.get("enabled"):
        return {"enabled": False, "alerted": 0}
    tg = get_telegram()
    if not tg.configured:
        return {"enabled": True, "alerted": 0, "skipped": "telegram не настроен"}

    # рабочее окно поддержки — то же, что в «Активности»
    from .routers.stats import _load_act_settings, _parse_hhmm, _working_seconds
    act = await _load_act_settings()
    start_min = _parse_hhmm(act["work_start"], "work_start")
    end_min = _parse_hhmm(act["work_end"], "work_end")
    tz_off = act.get("tz_offset_hours", 3)

    settings = get_settings()
    db = get_db()
    msgs = db[settings.support_messages_collection]
    users = db[settings.users_collection]
    states = db["ticket_escalations"]
    now = utcnow()

    waiting = await _waiting_chains(msgs, now - timedelta(days=LOOKBACK_DAYS))

    # состояние по уже неактуальным диалогам подчищаем (ответили/закрыли)
    stale_uids = [d["_id"] async for d in states.find({}, {"_id": 1})
                  if d["_id"] not in waiting]
    if stale_uids:
        await states.delete_many({"_id": {"$in": stale_uids}})
    if not waiting:
        return {"enabled": True, "alerted": 0}

    threshold = float(cfg["minutes"]) * 60
    repeat = timedelta(minutes=float(cfg["repeat_minutes"]))
    due: list[dict] = []
    for uid, chain_ts in waiting.items():
        waited = _working_seconds(chain_ts, now, start_min, end_min, tz_off)
        if waited < threshold:
            continue
        state = await states.find_one({"_id": uid})
        same_chain = state and _as_utc(state.get("chain_ts")) == chain_ts
        if same_chain:
            last = _as_utc(state.get("last_alert_at"))
            if last and now - last < repeat:
                continue
        due.append({"uid": uid, "chain_ts": chain_ts,
                    "waited_min": int(waited // 60),
                    "repeat": bool(same_chain)})
    if not due:
        return {"enabled": True, "alerted": 0}

    # закрытые тикеты не эскалируем (пользователь закрыл сам / уборка)
    infos: dict[int, dict] = {}
    uids = [d["uid"] for d in due]
    async for u in users.find(
            {"user_data.user_id": {"$in": uids}},
            {"user_data.user_id": 1, "user_data.username": 1,
             "user_data.first_name": 1, "info.support.status": 1}):
        infos[(u.get("user_data") or {}).get("user_id")] = u
    due = [d for d in due
           if ((infos.get(d["uid"], {}).get("info") or {}).get("support") or {})
           .get("status") in ("pending", "open")]
    if not due:
        return {"enabled": True, "alerted": 0}

    due.sort(key=lambda d: -d["waited_min"])
    base = (settings.panel_public_url or "").rstrip("/")
    lines = [f"⚠️ <b>Тикеты ждут ответа дольше {cfg['minutes']:g} мин:</b>"]
    for d in due[:20]:
        ud = (infos.get(d["uid"], {}).get("user_data") or {})
        name = ud.get("first_name") or ""
        uname = f" @{ud['username']}" if ud.get("username") else ""
        link = f"\n   {base}/#/ticket/{d['uid']}" if base else ""
        again = " (повторно!)" if d["repeat"] else ""
        lines.append(f"• #{d['uid']} {name}{uname} — {d['waited_min']} мин{again}{link}")
    if len(due) > 20:
        lines.append(f"… и ещё {len(due) - 20}")

    try:
        await tg.send_to_chat("\n".join(lines))
    except TelegramError as e:
        log.warning("escalation: не удалось отправить алерт: %s", e.message)
        return {"enabled": True, "alerted": 0, "error": e.message}

    for d in due:
        await states.update_one(
            {"_id": d["uid"]},
            {"$set": {"chain_ts": d["chain_ts"], "last_alert_at": now},
             "$inc": {"alerts": 1}},
            upsert=True)
    return {"enabled": True, "alerted": len(due)}


async def escalation_loop(interval: float = 60.0, initial_delay: float = 20.0) -> None:
    """Бесконечный цикл для lifespan: ошибки логируются, задача не умирает."""
    await asyncio.sleep(initial_delay)
    while True:
        try:
            r = await escalation_pass()
            if r.get("alerted"):
                log.info("escalation: отправлен алерт по %s тикетам", r["alerted"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("escalation: проход не удался (повтор через %s с)", interval)
        await asyncio.sleep(interval)
