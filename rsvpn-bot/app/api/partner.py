"""Вебхуки партнёрских ботов.

Один маршрут на всех: кто именно пишет, определяется секретом в адресе.
Отдельного процесса партнёрским ботам не нужно — они умеют только
поздороваться и дать кнопку в основного бота.

Отвечаем 200 всегда. Telegram на не-2xx повторяет доставку, и одна наша
ошибка превратилась бы в поток повторов по чужому боту.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.partner_bots import SECRET_HEADER, WEBHOOK_PATH

log = logging.getLogger(__name__)
router = APIRouter(tags=['partner'])


@router.post(WEBHOOK_PATH)
async def partner_webhook(secret: str, request: Request):
    container = request.app.state.container
    service = getattr(container, 'partner_bots', None)
    if service is None:
        return JSONResponse({'ok': True, 'note': 'disabled'})

    # Заголовок Telegram проставляет сам по secret_token из setWebhook. Адрес
    # мог утечь в логи прокси, заголовок — нет, поэтому сверяем оба.
    if request.headers.get(SECRET_HEADER) != secret:
        log.warning('партнёрский вебхук: секрет в заголовке не совпал')
        return JSONResponse({'ok': True, 'note': 'bad_secret'})

    try:
        update = json.loads((await request.body()).decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JSONResponse({'ok': True, 'note': 'bad_json'})

    try:
        known = await service.handle(secret, update)
    except Exception:
        log.exception('партнёрский вебхук не обработан')
        return JSONResponse({'ok': True, 'note': 'error'})

    return JSONResponse({'ok': True, 'note': 'done' if known else 'unknown_bot'})
