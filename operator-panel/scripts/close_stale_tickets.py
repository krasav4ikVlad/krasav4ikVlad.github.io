"""Массовое закрытие неактивных тикетов (разовая уборка).

Закрывает тикеты в статусах pending/open, в которых давно не было НИКАКИХ
сообщений (по общей истории support_messages; если истории нет — по
info.support.pending_at; если нет и его — тикет считается древним).

Пользователям НИЧЕГО не отправляется (ни личных сообщений, ни запроса
оценки) — это тихая уборка. В историю тикета пишется системная запись
«Тикет закрыт (неактивность)» без operator_login: баллы за такие закрытия
никому не начисляются, а страница «Активность» перестаёт считать их очередью.

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

    stale: list[dict] = []
    cursor = users.find(
        {"info.support.status": {"$in": ["pending", "open"]},
         "info.support.thread_id": {"$exists": True}},
        {"user_data.user_id": 1, "user_data.username": 1, "info.support": 1},
    )
    async for u in cursor:
        uid = (u.get("user_data") or {}).get("user_id")
        support = ((u.get("info") or {}).get("support")) or {}
        if uid is None:
            continue
        last = await msgs.find({"user_id": uid}, {"timestamp": 1}) \
            .sort("timestamp", -1).limit(1).to_list(1)
        last_ts = _as_utc(last[0].get("timestamp")) if last else _as_utc(support.get("pending_at"))
        if last_ts is None or last_ts < cutoff:
            stale.append({
                "uid": uid,
                "username": (u.get("user_data") or {}).get("username"),
                "status": (support.get("status") or "").lower(),
                "thread_id": support.get("thread_id"),
                "last_ts": last_ts,
            })
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
        from app.telegram import get_telegram
        tg = get_telegram()

    closed = renamed = 0
    for s in stale:
        res = await users.update_one(
            # защита от гонки: если пользователь только что написал и бот
            # перевёл тикет в pending заново — статус мог измениться
            {"user_data.user_id": s["uid"],
             "info.support.status": {"$in": ["pending", "open"]}},
            {"$set": {"info.support.status": "closed"}},
        )
        if getattr(res, "modified_count", 0) == 0:
            continue
        closed += 1
        await msgs.insert_one({
            "user_id": s["uid"], "direction": "system",
            "text": "Тикет закрыт (неактивность)",
            "attachment": None, "operator_login": None,
            "source": "site", "timestamp": utcnow(),
        })
        if tg is not None and s.get("thread_id"):
            try:
                await tg.set_thread_status_title(s["thread_id"], s["uid"], "closed")
                renamed += 1
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
