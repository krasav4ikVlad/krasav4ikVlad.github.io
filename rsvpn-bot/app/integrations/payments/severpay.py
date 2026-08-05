"""SeverPay. Подпись: hmac-sha256 по json без поля sign.

Два вебхука в старом коде (`webhook_sever` и `webhook_sever_web`) отличались
ровно одним — каким ключом проверять подпись. Здесь это один класс с двумя
экземплярами, а не 76 строк копипасты.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.integrations.payments.base import PaymentProvider, SignatureError, WebhookEvent

PAID_STATUSES = {'paid', 'success', 'succeeded'}


class SeverPayProvider(PaymentProvider):
    title = '⚡️ СБП'

    def __init__(self, api_key: str, code: str = 'severpay', http=None):
        self.code = code
        self._key = api_key
        self._http = http

    def verify(self, body: bytes, headers: dict[str, str], payload: dict) -> None:
        got = payload.get('sign')
        if not got:
            raise SignatureError('no sign')

        unsigned = {k: v for k, v in payload.items() if k != 'sign'}
        raw = json.dumps(unsigned, ensure_ascii=False, separators=(',', ':')).encode()
        expected = hmac.new(self._key.encode(), raw, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(str(got), expected):
            raise SignatureError('bad sign')

    def parse(self, payload: dict) -> WebhookEvent:
        if payload.get('type') != 'payin':
            return WebhookEvent.ignore('not_payin')

        data = payload.get('data') or {}
        if str(data.get('status', '')).lower() not in PAID_STATUSES:
            return WebhookEvent.ignore('not_paid')

        order_id = str(data.get('order_id') or '')
        left = order_id.split('_', 1)[0]
        user_id = int(left) if left.isdigit() else None

        try:
            amount = int(float(data.get('amount', 0)))
        except (TypeError, ValueError):
            return WebhookEvent.ignore('bad_amount')

        if not user_id or amount <= 0:
            return WebhookEvent.ignore('no_user_or_amount')

        return WebhookEvent(handled=True, txid=str(data.get('id') or order_id),
                            user_id=user_id, amount=amount, raw=payload)
