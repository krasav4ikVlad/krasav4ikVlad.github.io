"""WebSocket endpoint for the live event feed."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import decode_token
from ..config import get_settings
from ..ws import hub

log = logging.getLogger("app.ws")
router = APIRouter(tags=["ws"])


def _origin_allowed(ws: WebSocket) -> bool:
    """Reject cross-site WebSocket hijacking: the Origin (when a browser
    sends one) must match our host or a configured CORS origin."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser client
    host = ws.headers.get("host", "")
    origin_host = origin.split("://", 1)[-1]
    if origin_host == host:
        return True
    return origin in get_settings().cors_origin_list


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    if not _origin_allowed(ws):
        await ws.close(code=4403)
        return
    token = ws.cookies.get(get_settings().cookie_name)
    if not token or not decode_token(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.register(ws)
    try:
        while True:
            # client messages are only pings; ignore content
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(ws)
