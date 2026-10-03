"""Один эндпоинт на все платёжные системы.

    POST /payment/webhook/{provider}

Шесть почти одинаковых обработчиков схлопываются в один: маршрут находит
провайдера, провайдер проверяет подпись и разбирает тело, дальше общий путь
зачисления в TopupService.

Старые адреса (/payment/webhook_cardlink и прочие) остаются рабочими — см.
LEGACY_ROUTES ниже, чтобы не переписывать настройки в кабинетах платёжек
одновременно с выкладкой.

⚠️ У cardlink, wata и cards_ru подпись сейчас не проверяется (provider.verified
= False): любой, кто знает адрес, может отправить «оплату». Это поведение
перенесено как есть, чтобы ничего не сломать, но лечится в первую очередь —
у всех трёх есть механизм подписи в документации.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.integrations.payments.base import SignatureError

log = logging.getLogger(__name__)
router = APIRouter(prefix='/payment', tags=['payments'])

LEGACY_ROUTES = {
    'webhook_cardlink': 'cardlink',
    'webhook_wata': 'wata',
    'webhook_heleket': 'heleket',
    'webhook_sever': 'severpay',
    'webhook_sever_web': 'severpay_web',
    'webhook_tribute': 'tribute',
    'webhook': 'cards_ru',
}


async def handle(container, provider_code: str, request: Request) -> JSONResponse:
    provider = container.payments.get(provider_code)
    if provider is None:
        raise HTTPException(status_code=404, detail='Unknown provider')

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    payload = parse_body(body, headers)

    try:
        provider.verify(body, headers, payload)
    except SignatureError as exc:
        log.error('[%s] неверная подпись вебхука: %s', provider_code, exc)
        raise HTTPException(status_code=403, detail='Bad signature')

    event = provider.parse(payload)
    if not event.handled:
        return JSONResponse({'ok': True, 'note': event.note})

    user_id = await provider.resolve_user(event, container)
    if not user_id:
        log.error('[%s] не определён пользователь, txid=%s', provider_code, event.txid)
        return JSONResponse({'ok': True, 'note': 'manual_processing'})

    result = await container.topup.process(
        provider=provider_code, txid=event.txid, amount=event.amount,
        user_id=user_id, payload=event.raw,
    )
    return JSONResponse(result)


@router.post('/webhook/{provider_code}')
async def payment_webhook(provider_code: str, request: Request):
    return await handle(request.app.state.container, provider_code, request)


@router.post('/{legacy_path}')
async def legacy_webhook(legacy_path: str, request: Request):
    """Совместимость со старыми адресами вебхуков."""
    provider_code = LEGACY_ROUTES.get(legacy_path)
    if not provider_code:
        raise HTTPException(status_code=404, detail='Unknown route')
    return await handle(request.app.state.container, provider_code, request)


def parse_body(body: bytes, headers: dict[str, str]) -> dict:
    """JSON или form-urlencoded — платёжки шлют и то, и другое."""
    text = body.decode('utf-8', errors='ignore')
    if 'application/json' in headers.get('content-type', ''):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    if text.startswith('{'):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return dict(parse_qsl(text))
