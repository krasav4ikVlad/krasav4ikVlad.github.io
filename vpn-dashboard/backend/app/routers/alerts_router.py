"""Alerts router: recent alert feed, test notification and engine status.

``GET /alerts/recent`` reads the ``alerts`` collection written by
:class:`app.alerts.AlertEngine` (sorted newest first). ``POST /alerts/test``
sends a test message through the same Telegram channel the engine uses and
reports whether the delivery succeeded. ``GET /alerts/status`` exposes the
engine configuration: whether Telegram is set up, which checks run and the
dedup cooldown. None of these endpoints are cached — the feed is small and
must be live.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from ..agg import as_utc, iso
from ..alerts import send_telegram
from ..config import get_settings
from ..db import ALERTS, get_db

log = logging.getLogger("app.alerts_router")
router = APIRouter(prefix="/alerts", tags=["alerts"])

# Checks implemented by app.alerts.AlertEngine.run_all (contract order).
_CHECKS: list[str] = [
    "provider_silence",
    "revenue_anomaly",
    "promo_abuse",
    "error_burst",
]


# ---------------------------------------------------------------------------
# GET /alerts/recent — latest alerts, newest first (not cached)
# ---------------------------------------------------------------------------

@router.get("/recent")
async def recent(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Latest fired alerts from the ``alerts`` collection."""
    db = get_db()
    cursor = (db[ALERTS]
              .find({}, {"_id": 0})
              .sort("created_at", -1)
              .limit(limit))

    alerts: list[dict[str, Any]] = []
    async for doc in cursor:
        details = doc.get("details")
        alerts.append({
            "key": doc.get("key"),
            "severity": doc.get("severity"),
            "title": doc.get("title"),
            "details": details if isinstance(details, dict) else {},
            "created_at": iso(as_utc(doc.get("created_at"))
                              if isinstance(doc.get("created_at"), datetime)
                              else None),
        })
    return {"alerts": alerts}


# ---------------------------------------------------------------------------
# POST /alerts/test — send a test Telegram message (not cached)
# ---------------------------------------------------------------------------

@router.post("/test")
async def test() -> dict[str, Any]:
    """Fire a test message into the admin Telegram chat."""
    now = datetime.now(timezone.utc)
    sent = await send_telegram(
        "✅ <b>Тестовое уведомление</b>\n"
        "Канал алертов VPN-дашборда работает.\n"
        f"Время: {now:%Y-%m-%d %H:%M:%S} UTC"
    )
    return {"sent": bool(sent)}


# ---------------------------------------------------------------------------
# GET /alerts/status — engine configuration (not cached)
# ---------------------------------------------------------------------------

@router.get("/status")
async def status() -> dict[str, Any]:
    """Alert engine status: Telegram config, active checks, cooldown."""
    s = get_settings()
    telegram_configured = bool(s.tg_bot_token) and bool(s.tg_admin_chat_id)
    return {
        "telegram_configured": telegram_configured,
        "checks": list(_CHECKS),
        "cooldown_hours": float(s.alert_cooldown_hours),
    }
