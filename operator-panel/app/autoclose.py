"""Автозакрытие тикетов, в которых пользователь замолчал после ответа оператора.

Фоновая задача панели (запускается в lifespan): раз в несколько минут ищет
тикеты в статусе open, где ПОСЛЕДНЕЕ сообщение диалога — от оператора и
старше N часов (настройка владельца: «Настройки расчёта» на странице
«Активность»). Такой тикет закрывается, а пользователю в Telegram уходит
сообщение с кнопками оценки rate:{1..5} и кнопкой «Вопрос не решён»
(callback support:reopen) — обе обрабатывает бот (см. docs/bot-integration.md).
Нажал «не решён» → бот вернёт статус open, и тикет снова в работе.

Тикеты, где последним писал ПОЛЬЗОВАТЕЛЬ, автозакрытие не трогает:
это неотвеченная очередь — она должна висеть и тянуть норму вверх,
а не тихо исчезать. Статус pending тоже не трогаем: короткоживущее
«ждёт выбора меню» бот закрывает сам через 5 минут.

Закрытие системное, без operator_login. Закрытия тикетов вообще не влияют
на баллы и норму «Активности» — раз тикеты закрываются сами, оцениваются
только ответы, их скорость и оценки пользователей.
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

DEFAULT_AUTOCLOSE = {
    "enabled": True,
    "hours": 24.0,  # закрывать, если пользователь молчит дольше N часов
}

REOPEN_CALLBACK = "support:reopen"


def rating_reopen_keyboard() -> dict:
    """Оценка 1..5 + «Вопрос не решён» — оба callback'а обрабатывает бот."""
    return {
        "inline_keyboard": [
            [{"text": str(i), "callback_data": f"rate:{i}"} for i in range(1, 6)],
            [{"text": "Вопрос не решён — открыть заново",
              "callback_data": REOPEN_CALLBACK}],
        ]
    }


async def load_autoclose_settings() -> dict:
    doc = await get_db()["panel_settings"].find_one({"_id": "autoclose"}) or {}
    return {**DEFAULT_AUTOCLOSE,
            **{k: doc[k] for k in DEFAULT_AUTOCLOSE if k in doc}}


def _as_utc(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _last_message(msgs, uid) -> dict | None:
    docs = await msgs.find({"user_id": uid}).sort("timestamp", -1).limit(1).to_list(1)
    return docs[0] if docs else None


async def autoclose_pass() -> dict:
    """Один проход автозакрытия. Возвращает статистику (для логов и тестов)."""
    cfg = await load_autoclose_settings()
    if not cfg.get("enabled"):
        return {"enabled": False, "closed": 0}
    tg = get_telegram()
    if not tg.configured:
        return {"enabled": True, "closed": 0, "skipped": "telegram не настроен"}

    settings = get_settings()
    db = get_db()
    users = db[settings.users_collection]
    msgs = db[settings.support_messages_collection]
    hours = float(cfg.get("hours") or DEFAULT_AUTOCLOSE["hours"])
    cutoff = utcnow() - timedelta(hours=hours)

    threads: dict = {}
    async for u in users.find(
            {"info.support.status": "open",
             "info.support.thread_id": {"$exists": True}},
            {"user_data.user_id": 1, "info.support.thread_id": 1}):
        uid = (u.get("user_data") or {}).get("user_id")
        if uid is not None:
            threads[uid] = ((u.get("info") or {}).get("support") or {}).get("thread_id")
    if not threads:
        return {"enabled": True, "closed": 0}

    # последнее сообщение каждого открытого диалога — одним запросом на 1000
    candidates: list = []
    uids = list(threads)
    for i in range(0, len(uids), 1000):
        async for d in msgs.aggregate([
            {"$match": {"user_id": {"$in": uids[i:i + 1000]}}},
            {"$sort": {"timestamp": 1}},
            {"$group": {"_id": "$user_id",
                        "last_ts": {"$last": "$timestamp"},
                        "last_dir": {"$last": "$direction"}}},
        ]):
            ts = _as_utc(d.get("last_ts"))
            if d.get("last_dir") == "operator" and ts is not None and ts < cutoff:
                candidates.append(d["_id"])

    hours_txt = f"{hours:g}"
    closed = 0
    for uid in candidates:
        # защита от гонки: пока шёл проход, пользователь мог написать
        last = await _last_message(msgs, uid)
        ts = _as_utc((last or {}).get("timestamp"))
        if not last or last.get("direction") != "operator" or ts is None or ts >= cutoff:
            continue
        res = await users.update_one(
            {"user_data.user_id": uid, "info.support.status": "open"},
            {"$set": {"info.support.status": "closed"}})
        if getattr(res, "modified_count", 0) == 0:
            continue
        closed += 1
        await msgs.insert_one({
            "user_id": uid, "direction": "system",
            "text": "Тикет закрыт (авто): пользователь не ответил",
            "attachment": None, "operator_login": None,
            "source": "site", "timestamp": utcnow(),
        })
        thread_id = threads.get(uid)
        if thread_id:
            await tg.set_thread_status_title(thread_id, uid, "closed")
            try:
                await tg.send_to_thread(
                    thread_id,
                    f"🔴 <b>Тикет закрыт автоматически</b> "
                    f"(пользователь не отвечает больше {hours_txt} ч).")
            except TelegramError:
                pass
        try:
            await tg.send_to_user(
                uid,
                "✅ <b>Ваш тикет закрыт автоматически</b> — от вас давно не было "
                "ответа.\n\nЕсли вопрос решён, пожалуйста, оцените работу "
                "поддержки. Если нет — нажмите «Вопрос не решён», и тикет "
                "откроется снова.",
                reply_markup=rating_reopen_keyboard())
        except TelegramError:
            pass  # пользователь мог заблокировать бота — тикет всё равно закрыт
    return {"enabled": True, "closed": closed, "checked": len(threads)}


async def autoclose_loop(interval: float = 600.0, initial_delay: float = 90.0) -> None:
    """Бесконечный цикл для lifespan: ошибки логируются, задача не умирает."""
    await asyncio.sleep(initial_delay)
    while True:
        try:
            r = await autoclose_pass()
            if r.get("closed"):
                log.info("autoclose: закрыто тикетов — %s", r["closed"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("autoclose: проход не удался (повтор через %s с)", interval)
        await asyncio.sleep(interval)
