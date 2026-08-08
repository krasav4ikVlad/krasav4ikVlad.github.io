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

from app.admin import health
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
        await container.health.mark(health.PANEL_WEBHOOK, note='bad_signature')
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
        await container.health.mark(health.PANEL_WEBHOOK, event=event, note='error')
        return JSONResponse({'ok': True, 'note': 'error'})

    # Строка на КАЖДОЕ событие, а не только на отправленное напоминание.
    # Иначе «панель не зовёт» и «зовёт, но событие отбрасывается» выглядят
    # в логе одинаково — пустотой, и искать причину приходится вслепую.
    note = str(result.get('note', ''))
    log.info('remnawave: %s → %s', event or 'без события', note)

    await container.health.mark(health.PANEL_WEBHOOK, event=event, note=note)
    if note.startswith('sent_'):
        await container.health.mark(health.EXPIRY_SENT, key=note[5:],
                                    user_id=result.get('user_id'),
                                    delivered=result.get('delivered'))
    return JSONResponse(result)
