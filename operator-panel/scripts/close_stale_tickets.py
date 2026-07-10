"""Массовое закрытие неактивных тикетов (разовая уборка).

Закрывает тикеты в статусах pending/open, в которых давно не было НИКАКИХ
сообщений (по общей истории support_messages; если истории нет — по
info.support.pending_at; если нет и его — тикет считается древним).

Пользователям НИЧЕГО не отправляется (ни личных сообщений, ни запроса
оценки) — это тихая уборка. Тикетам, у которых есть история сообщений,
пишется системная запись «Тикет закрыт (неактивность)» без operator_login:
баллы за такие закрытия никому не начисляются. Древним тикетам без истории
запись не пишется, чтобы не раздувать support_messages. Если пользователь
напишет снова — бот как обычно откроет тикет заново.

Запуск на сервере из каталога панели:
    cd /opt/operator-panel

    # 1) ПРЕДПРОСМОТР — ничего не меняет, просто показывает, что будет закрыто
    venv/bin/python scripts/close_stale_tickets.py --days 7

    # 2) Реальное закрытие
    venv/bin/python scripts/close_stale_tickets.py --days 7 --yes

    # 3) То же + переименовать треды в 🔴 (медленно: ~2 треда/сек из-за
    #    лимитов Telegram; можно пропустить — заголовки поправятся при
    #    следующей активности тикета)
    venv/bin/python scripts/close_stale_tickets.py --days 7 --yes --rename-threads
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.database import close_client, get_db  # noqa: E402
from app.utils import utcnow  # noqa: E402


def _as_utc(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def collect_stale(days: float) -> list[dict]:
    settings = get_settings()
    db = get_db()
    users = db[settings.users_collection]
    msgs = db[settings.support_messages_collection]
    cutoff = utcnow() - timedelta(days=days)

    # 1) кто писал/получал сообщения за период — ОДНИМ запросом
    #    (раньше был запрос на каждый тикет — на тысячах тикетов с удалённой
    #    Mongo это выглядело как зависание)
    print("Ищу пользователей с недавней активностью…", flush=True)
    active = set(await msgs.distinct("user_id", {"timestamp": {"$gte": cutoff}}))
    print(f"  активных за последние {days:g} дн.: {len(active)}", flush=True)

    stale: list[dict] = []
    scanned = 0
    cursor = users.find(
        {"info.support.status": {"$in": ["pending", "open"]},
         "info.support.thread_id": {"$exists": True}},
        {"user_data.user_id": 1, "user_data.username": 1, "info.support": 1},
    )
    async for u in cursor:
        scanned += 1
        if scanned % 2000 == 0:
            print(f"  просмотрено тикетов: {scanned}…", flush=True)
        uid = (u.get("user_data") or {}).get("user_id")
        support = ((u.get("info") or {}).get("support")) or {}
        if uid is None or uid in active:
            continue
        pending_at = _as_utc(support.get("pending_at"))
        if pending_at is not None and pending_at >= cutoff:
            continue  # свежий тикет, у которого просто нет сообщений в истории
        stale.append({
            "uid": uid,
            "username": (u.get("user_data") or {}).get("username"),
            "status": (support.get("status") or "").lower(),
            "thread_id": support.get("thread_id"),
            "last_ts": pending_at,  # уточним из истории ниже
        })
    print(f"  открытых/ожидающих тикетов всего: {scanned}", flush=True)

    # 2) даты последней активности — только для кандидатов, пачками
    uids = [s["uid"] for s in stale]
    last_map: dict = {}
    for i in range(0, len(uids), 5000):
        async for d in msgs.aggregate([
            {"$match": {"user_id": {"$in": uids[i:i + 5000]}}},
            {"$group": {"_id": "$user_id", "last": {"$max": "$timestamp"}}},
        ]):
            last_map[d["_id"]] = _as_utc(d["last"])
    for s in stale:
        # has_history: у тикета есть сообщения в общей истории — только таким
        # пишем системную запись о закрытии (древним тикетам без истории она
        # не нужна, а сотни тысяч записей замедлили бы страницу «Активность»)
        s["has_history"] = s["uid"] in last_map
        s["last_ts"] = last_map.get(s["uid"]) or s["last_ts"]
    return stale


async def run(days: float, apply: bool, rename_threads: bool) -> int:
    settings = get_settings()
    db = get_db()
    users = db[settings.users_collection]
    msgs = db[settings.support_messages_collection]

    stale = await collect_stale(days)
    n_pending = sum(1 for s in stale if s["status"] == "pending")
    n_open = len(stale) - n_pending
    print(f"Тикетов без активности больше {days:g} дн.: {len(stale)} "
          f"(ожидают: {n_pending}, у оператора: {n_open})")
    for s in stale[:20]:
        last = s["last_ts"].strftime("%d.%m.%Y %H:%M") if s["last_ts"] else "никогда"
        uname = f"@{s['username']}" if s.get("username") else ""
        print(f"  #{s['uid']} {uname:24s} {s['status']:8s} последняя активность: {last}")
    if len(stale) > 20:
        print(f"  … и ещё {len(stale) - 20}")

    if not stale:
        return 0
    if not apply:
        print("\nЭто ПРЕДПРОСМОТР — ничего не изменено. "
              "Добавьте --yes, чтобы закрыть перечисленные тикеты.")
        return 0

    tg = None
    if rename_threads:
        est_h = len(stale) * 0.5 / 3600
        if est_h >= 1:
            print(f"\nВНИМАНИЕ: --rename-threads на {len(stale)} тикетах займёт "
                  f"~{est_h:.0f} ч (лимиты Telegram). Обычно его стоит пропустить: "
                  "заголовки тредов поправятся сами при следующей активности.")
        from app.telegram import get_telegram
        tg = get_telegram()

    # закрываем пачками: два запроса к Mongo на 1000 тикетов вместо двух на каждый
    closed = renamed = 0
    chunk_size = 1000
    for i in range(0, len(stale), chunk_size):
        chunk = stale[i:i + chunk_size]
        res = await users.update_many(
            # защита от гонки: закрываем только тех, кто всё ещё pending/open
            {"user_data.user_id": {"$in": [s["uid"] for s in chunk]},
             "info.support.status": {"$in": ["pending", "open"]}},
            {"$set": {"info.support.status": "closed"}},
        )
        n = getattr(res, "modified_count", None)
        closed += len(chunk) if n is None else n
        now = utcnow()
        records = [{
            "user_id": s["uid"], "direction": "system",
            "text": "Тикет закрыт (неактивность)",
            "attachment": None, "operator_login": None,
            "source": "site", "timestamp": now,
        } for s in chunk if s.get("has_history")]
        if records:
            await msgs.insert_many(records)
        print(f"  закрыто: {min(i + chunk_size, len(stale))} из {len(stale)}…", flush=True)

    if tg is not None:
        for s in stale:
            if not s.get("thread_id"):
                continue
            try:
                await tg.set_thread_status_title(s["thread_id"], s["uid"], "closed")
                renamed += 1
                if renamed % 100 == 0:
                    print(f"  тредов переименовано: {renamed}…", flush=True)
            except Exception:
                pass  # тред мог быть удалён — не критично
            await asyncio.sleep(0.5)  # лимиты Telegram

    print(f"\nЗакрыто: {closed}" + (f", тредов переименовано: {renamed}" if rename_threads else ""))
    return closed


def cli() -> None:
    ap = argparse.ArgumentParser(description="Массовое закрытие неактивных тикетов")
    ap.add_argument("--days", type=float, default=7,
                    help="закрывать тикеты без активности дольше N дней (по умолчанию 7)")
    ap.add_argument("--yes", action="store_true",
                    help="применить изменения (без флага — только предпросмотр)")
    ap.add_argument("--rename-threads", action="store_true",
                    help="переименовать треды в Telegram в 🔴 (медленно)")
    args = ap.parse_args()
    if args.days <= 0:
        ap.error("--days должен быть больше нуля")

    async def _main():
        try:
            await run(args.days, args.yes, args.rename_threads)
        finally:
            await close_client()

    asyncio.run(_main())


if __name__ == "__main__":
    cli()
