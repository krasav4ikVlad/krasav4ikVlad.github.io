"""Alert engine: runs on a schedule, stores alerts, notifies the admin in
Telegram and pushes them into the live WS feed.

Checks:

1. **Provider silence** — a payment source with regular traffic has produced
   no successful top-up for longer than ``max(PROVIDER_SILENCE_HOURS,
   4 × median gap)``.
2. **Revenue anomaly** — yesterday's revenue deviates more than 2σ from the
   trailing 30-day mean.
3. **Promo abuse** — one user activated the same code ≥ 3 times in 24h.
4. **Error burst** — optional: if an ``errors`` collection exists, more than
   50 error documents in the last 15 minutes.

Alerts are deduplicated by key with a cooldown so the admin isn't spammed.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from .config import get_settings
from .db import ALERTS, TX_FLAT
from .ws import EventHub, hub as global_hub

log = logging.getLogger("app.alerts")


async def send_telegram(text: str) -> bool:
    s = get_settings()
    if not s.tg_bot_token or not s.tg_admin_chat_id:
        return False
    url = f"https://api.telegram.org/bot{s.tg_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": s.tg_admin_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            return resp.status_code == 200
    except httpx.HTTPError as e:
        log.warning("telegram send failed", extra={"error": str(e)})
        return False


class AlertEngine:
    def __init__(self, db: AsyncIOMotorDatabase, hub: EventHub | None = None):
        self.db = db
        self.hub = hub or global_hub

    async def _fire(self, key: str, severity: str, title: str,
                    details: dict[str, Any] | None = None) -> bool:
        """Store + notify unless the same key fired within the cooldown."""
        s = get_settings()
        now = datetime.now(timezone.utc)
        cooldown_from = now - timedelta(hours=s.alert_cooldown_hours)
        recent = await self.db[ALERTS].find_one(
            {"key": key, "created_at": {"$gte": cooldown_from}})
        if recent:
            return False

        doc = {
            "key": key,
            "severity": severity,
            "title": title,
            "details": details or {},
            "created_at": now,
        }
        await self.db[ALERTS].insert_one(dict(doc))
        icon = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(severity, "⚠️")
        await send_telegram(f"{icon} <b>{title}</b>\n"
                            + "\n".join(f"{k}: {v}" for k, v in
                                        (details or {}).items()))
        doc.pop("_id", None)
        doc["created_at"] = now.isoformat()
        await self.hub.publish("alert", doc)
        log.warning("alert fired", extra={"key": key, "title": title})
        return True

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def check_provider_silence(self) -> None:
        s = get_settings()
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=14)
        pipeline = [
            {"$match": {"direction": "credit", "kind": "topup",
                        "source": {"$type": "string"},
                        "dt": {"$gte": since}}},
            {"$sort": {"dt": -1}},
            {"$group": {"_id": "$source",
                        "count": {"$sum": 1},
                        "dts": {"$push": "$dt"}}},
        ]
        async for row in self.db[TX_FLAT].aggregate(pipeline):
            source, count = row["_id"], row["count"]
            if count < 5:
                continue  # too rare to reason about "usual frequency"
            dts = sorted(d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
                         for d in row["dts"][:50])
            gaps = [(b - a).total_seconds() / 3600
                    for a, b in zip(dts, dts[1:]) if b > a]
            median_gap_h = statistics.median(gaps) if gaps else 24.0
            threshold_h = max(s.provider_silence_hours, 4 * median_gap_h)
            silence_h = (now - dts[-1]).total_seconds() / 3600
            if silence_h > threshold_h:
                await self._fire(
                    f"provider_silence:{source}", "critical",
                    f"Провайдер {source} молчит {silence_h:.1f} ч",
                    {"источник": source,
                     "тишина_часов": round(silence_h, 1),
                     "обычный_интервал_часов": round(median_gap_h, 1),
                     "порог_часов": round(threshold_h, 1)})

    async def check_revenue_anomaly(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = today - timedelta(days=31)
        pipeline = [
            {"$match": {"direction": "credit", "kind": "topup",
                        "dt": {"$gte": since, "$lt": today}}},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$dt", "unit": "day"}},
                "revenue": {"$sum": {"$subtract": ["$amount", "$bonus"]}},
            }},
            {"$sort": {"_id": 1}},
        ]
        rows = [r async for r in self.db[TX_FLAT].aggregate(pipeline)]
        if len(rows) < 8:
            return
        values = [r["revenue"] for r in rows]
        yesterday_value = values[-1]
        history = values[:-1]
        mean = statistics.fmean(history)
        stdev = statistics.pstdev(history)
        if stdev <= 0:
            return
        z = (yesterday_value - mean) / stdev
        if abs(z) > 2:
            day = rows[-1]["_id"]
            direction = "всплеск" if z > 0 else "провал"
            await self._fire(
                f"revenue_anomaly:{day:%Y-%m-%d}", "warning",
                f"Аномалия выручки: {direction} ({z:+.1f}σ)",
                {"день": f"{day:%Y-%m-%d}",
                 "выручка": round(yesterday_value, 2),
                 "среднее_30д": round(mean, 2),
                 "сигма": round(stdev, 2)})

    async def check_promo_abuse(self) -> None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        pipeline = [
            {"$match": {"kind": "promo", "promo_code": {"$type": "string"},
                        "dt": {"$gte": since}}},
            {"$group": {"_id": {"user": "$user_id", "code": "$promo_code"},
                        "count": {"$sum": 1},
                        "total": {"$sum": "$amount"}}},
            {"$match": {"count": {"$gte": 3}}},
            {"$limit": 20},
        ]
        async for row in self.db[TX_FLAT].aggregate(pipeline):
            user, code = row["_id"]["user"], row["_id"]["code"]
            await self._fire(
                f"promo_abuse:{user}:{code}", "critical",
                f"Абьюз промокода {code}",
                {"user_id": user, "код": code,
                 "активаций_за_24ч": row["count"],
                 "выдано_₽": round(row["total"], 2)})

    async def check_error_burst(self) -> None:
        names = await self.db.list_collection_names()
        if "errors" not in names:
            return
        since = datetime.now(timezone.utc) - timedelta(minutes=15)
        count = await self.db["errors"].count_documents(
            {"$or": [{"dt": {"$gte": since}}, {"created_at": {"$gte": since}}]})
        if count > 50:
            await self._fire(
                "error_burst", "critical",
                f"Всплеск ошибок: {count} за 15 минут",
                {"ошибок": count})

    async def run_all(self) -> None:
        for check in (self.check_provider_silence, self.check_revenue_anomaly,
                      self.check_promo_abuse, self.check_error_burst):
            try:
                await check()
            except Exception:
                log.exception("alert check failed",
                              extra={"check": check.__name__})
