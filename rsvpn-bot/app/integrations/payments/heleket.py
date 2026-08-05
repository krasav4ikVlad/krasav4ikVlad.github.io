"""Heleket (крипта). Подпись: md5(base64(json без sign) + api_key)."""

from __future__ import annotations

import base64
import hashlib
import json

from app.integrations.payments.base import PaymentProvider, SignatureError, WebhookEvent

PAID_STATUSES = {'paid', 'paid_over'}


class HeleketProvider(PaymentProvider):
    code = 'heleket'
    title = '💸 Криптовалюта'

    def __init__(self, api_key: str, merchant_id: str = '', http=None):
        self._key = api_key
        self._merchant_id = merchant_id
        self._http = http

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
