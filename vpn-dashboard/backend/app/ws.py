"""WebSocket event hub for the live feed.

Events are published by the change-stream watcher and the alert engine and
fanned out to every connected dashboard client. The last 100 events are kept
in a ring buffer and replayed on connect so a page refresh doesn't blank the
ticker.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("app.ws")


class EventHub:
    def __init__(self, replay_size: int = 100) -> None:
        self._clients: set[WebSocket] = set()
        self._recent: deque[dict] = deque(maxlen=replay_size)
        self._seq = itertools.count(1)
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._recent)[-limit:]

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        # replay recent events so the ticker isn't empty after refresh
        for event in list(self._recent):
            try:
                await ws.send_json({"type": "event", "replay": True, **event})
            except Exception:
                break

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "id": next(self._seq),
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }
        self._recent.append(event)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json({"type": "event", **event})
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)
        if dead:
            log.info("dropped dead ws clients", extra={"count": len(dead)})


hub = EventHub()
