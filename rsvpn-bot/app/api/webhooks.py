"""Один эндпоинт на все платёжные системы.

    POST /payment/webhook/{provider}

Вместо шести почти одинаковых обработчиков: маршрут находит провайдера,
провайдер проверяет подпись и разбирает тело, дальше общий путь зачисления.
Добавить новую платёжку = один класс в integrations/payments + строка в
контейнере, эндпоинт трогать не нужно.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.integrations.payments.base import SignatureError

log = logging.getLogger(__name__)
router = APIRouter(prefix='/payment', tags=['payments'])


@router.post('/webhook/{provider_code}')
async def payment_webhook(provider_code: str, request: Request):
    container = request.app.state.container

    provider = container.payments.get(provider_code)
    if provider is None:
        raise HTTPException(status_code=404, detail='Unknown provider')

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    try:
        provider.verify(body, headers)
    except SignatureError:
        log.error('[%s] неверная подпись вебхука', provider_code)
        raise HTTPException(status_code=403, detail='Bad signature')

    payload = _payload(body, headers)
    event = provider.parse(payload)

    if not event.handled:
        return JSONResponse({'ok': True, 'note': event.note})

    result = await container.topup.process(
        provider=provider_code,
        txid=event.txid,
        user_id=event.user_id,
        amount=event.amount,
        payload=event.raw,
    )
    return JSONResponse(result)


def _payload(body: bytes, headers: dict[str, str]) -> dict:
    content_type = headers.get('content-type', '')
    text = body.decode('utf-8', errors='ignore')
    if 'application/json' in content_type:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    from urllib.parse import parse_qsl
    return dict(parse_qsl(text))
