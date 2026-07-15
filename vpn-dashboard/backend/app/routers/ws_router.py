"""WebSocket endpoint for the live event feed."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import decode_token
from ..config import get_settings
from ..ws import hub

log = logging.getLogger("app.ws")
router = APIRouter(tags=["ws"])


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
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
