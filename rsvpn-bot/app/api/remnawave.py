"""Приём вебхуков панели Remnawave.

Один маршрут на все события панели: подпись проверяется здесь, дальше событие
уходит в сервис. Путь тот же, что был у lifeline (/remnawave/webhook), —
в настройках панели ничего менять не нужно.

Отвечаем 200 почти всегда: панель ретраит любой не-2xx, и одна наша ошибка
превратится в поток повторов. Неудачу видно в логе, а не в очереди ретраев.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import verify_hmac_signature

log = logging.getLogger(__name__)
router = APIRouter(tags=['remnawave'])

SIGNATURE_HEADER = 'x-remnawave-signature'
WEBHOOK_PATH = '/remnawave/webhook'


@router.post(WEBHOOK_PATH)
async def remnawave_webhook(request: Request):
    container = request.app.state.container
    body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)

    if not verify_hmac_signature(body, signature, container.config.vpn.webhook_secret):
        log.warning('remnawave: неверная подпись вебхука')
        return JSONResponse({'ok': True, 'note': 'bad_signature'})

    try:
        payload = json.loads(body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        log.warning('remnawave: тело не разобралось')
        return JSONResponse({'ok': True, 'note': 'bad_json'})

    event = payload.get('event') or ''
    data = payload.get('data') or {}
    meta = payload.get('meta') or {}

    try:
        result = await container.expiry.handle(event, data, meta)
    except Exception:
        log.exception('remnawave: ошибка обработки события %s', event)
        return JSONResponse({'ok': True, 'note': 'error'})

    return JSONResponse(result)
