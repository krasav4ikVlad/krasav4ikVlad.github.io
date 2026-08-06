"""Tribute (иностранные карты). Подпись: hmac-sha256 сырого тела.

Суммы приходят в минорных единицах. Валюты кроме рубля не зачисляются
автоматически — уходят на ручную обработку, как и было.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

from app.integrations.payments.base import (Invoice, PaymentProvider, SignatureError,
                                            WebhookEvent)

PAYMENT_EVENTS = {'new_donation', 'new_digital_product'}
UID_IN_MESSAGE = re.compile(r'\b(\d{6,15})\b')


class TributeProvider(PaymentProvider):
    code = 'tribute'
    title = '🌐 Карта иностранная'

    def __init__(self, api_key: str, http=None, app_url: str = ''):
        self._key = api_key
        self._http = http
        self._app_url = app_url or 'https://t.me/tribute/app?startapp=dNvx'

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        """У Tribute счёт не выставляется: пользователь платит в мини-аппе,
        а сумму мы узнаём из вебхука."""
        return Invoice(url=self._app_url, payment_id='', amount=amount)

    def verify(self, body: bytes, headers: dict[str, str], payload: dict) -> None:
        signature = (headers.get('trbt-signature') or '').strip()
        if not signature:
            raise SignatureError('no signature header')

        digest = hmac.new(self._key.encode(), body, hashlib.sha256)
        as_hex = digest.hexdigest()
        as_b64 = base64.b64encode(digest.digest()).decode()

        # формат digest в документации не зафиксирован — принимаем оба
        if not (hmac.compare_digest(signature.lower(), as_hex.lower())
                or hmac.compare_digest(signature, as_b64)):
            raise SignatureError('bad signature')

    def parse(self, payload: dict) -> WebhookEvent:
        name = payload.get('name')
        if name not in PAYMENT_EVENTS:
            return WebhookEvent.ignore(f'event_{name}')

        data = payload.get('payload') or {}

        user_id = data.get('telegram_user_id')
        try:
            user_id = int(user_id) if user_id else None
        except (TypeError, ValueError):
            user_id = None
        if not user_id:
            match = UID_IN_MESSAGE.search(data.get('message') or '')
            user_id = int(match.group(1)) if match else None

        currency = str(data.get('currency') or '').lower()
        if currency != 'rub':
            return WebhookEvent.ignore(f'manual_currency_{currency}')

        try:
            minor = int(data.get('amount') or 0)
        except (TypeError, ValueError):
            return WebhookEvent.ignore('bad_amount')
        if minor <= 0 or not user_id:
            return WebhookEvent.ignore('no_user_or_amount')

        # donation_request_id одинаков у всех платежей, created_at стабилен
        # между ретраями (в отличие от sent_at) — на них и строим txid
        ref = data.get('donation_request_id') or data.get('product_id') or 0
        txid = f'tribute_{name}_{ref}_{payload.get("created_at")}_{user_id}_{minor}'

        return WebhookEvent(handled=True, txid=txid, user_id=user_id,
                            amount=minor // 100, raw=payload)
