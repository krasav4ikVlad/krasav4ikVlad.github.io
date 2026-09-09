"""Heleket (крипта). Подпись: md5(base64(json без sign) + api_key)."""

from __future__ import annotations

import base64
import hashlib
import json

from app.integrations.payments.base import (Invoice, PaymentProvider, SignatureError,
                                            WebhookEvent)
from app.content.emoji import e

PAID_STATUSES = {'paid', 'paid_over'}


class HeleketProvider(PaymentProvider):
    code = 'heleket'
    title = f'{e("payout")} Криптовалюта'

    def __init__(self, api_key: str, merchant_id: str = '', http=None):
        self._key = api_key
        self._merchant_id = merchant_id
        self._http = http

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        from uuid import uuid4

        from app.core.errors import PaymentError

        payload = {
            'amount': str(amount), 'currency': 'RUB',
            'order_id': f'{user_id}_{uuid4()}',
            'url_success': self.success_url, 'url_return': self.success_url,
            'url_callback': f'{self.callback_base}/payment/webhook/heleket',
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        sign = hashlib.md5(
            (base64.b64encode(raw.encode()).decode() + self._key).encode()).hexdigest()

        data = await self._post(
            'https://api.heleket.com/v1/payment',
            headers={'Content-Type': 'application/json',
                     'merchant': self._merchant_id, 'sign': sign},
            content=raw.encode(),
        )

        result = data.get('result') or {}
        if not result.get('url'):
            raise PaymentError(f'heleket: нет ссылки {str(data)[:200]}')
        return Invoice(url=result['url'], payment_id=str(result.get('order_id') or ''),
                       amount=amount)

    def verify(self, body: bytes, headers: dict[str, str], payload: dict) -> None:
        got = payload.get('sign')
        if not got:
            raise SignatureError('no sign')

        unsigned = {k: v for k, v in payload.items() if k != 'sign'}
        raw = json.dumps(unsigned, ensure_ascii=False, separators=(',', ':')).replace('/', '\\/')
        encoded = base64.b64encode(raw.encode()).decode()
        expected = hashlib.md5((encoded + self._key).encode()).hexdigest()

        if expected != got:
            raise SignatureError(f'expected={expected} got={got}')

    def parse(self, payload: dict) -> WebhookEvent:
        if payload.get('status') not in PAID_STATUSES or not payload.get('is_final'):
            return WebhookEvent.ignore('not_paid')

        order_id = str(payload.get('order_id') or '')
        user_id = int(order_id.split('_')[0]) if order_id.split('_')[0].isdigit() else None

        try:
            amount = int(round(float(payload.get('amount', 0))))
        except (TypeError, ValueError):
            return WebhookEvent.ignore('bad_amount')

        if not user_id or amount <= 0:
            return WebhookEvent.ignore('no_user_or_amount')

        txid = payload.get('txid') or payload.get('uuid') or order_id
        return WebhookEvent(handled=True, txid=str(txid), user_id=user_id,
                            amount=amount, raw=payload)
