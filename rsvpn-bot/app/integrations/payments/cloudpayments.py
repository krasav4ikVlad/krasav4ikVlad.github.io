"""CloudPayments (карты РФ). Форма, id пользователя в AccountId."""

from __future__ import annotations

from app.integrations.payments.base import PaymentProvider, WebhookEvent


class CloudPaymentsProvider(PaymentProvider):
    code = 'cards_ru'
    title = '💳 Карта РФ'
    verified = False   # ⚠️ HMAC из заголовка Content-HMAC сейчас не проверяется

    def __init__(self, public_id: str = '', secret: str = '', http=None):
        self._public_id = public_id
        self._secret = secret
        self._http = http

    def parse(self, payload: dict) -> WebhookEvent:
        if payload.get('Status') != 'Completed':
            return WebhookEvent.ignore('not_completed')

        try:
            user_id = int(payload['AccountId'])
            amount = int(round(float(payload['Amount'])))
        except (KeyError, TypeError, ValueError):
            return WebhookEvent.ignore('bad_payload')

        if amount <= 0:
            return WebhookEvent.ignore('zero_amount')

        return WebhookEvent(handled=True, txid=str(payload.get('InvoiceId') or ''),
                            user_id=user_id, amount=amount, raw=payload)
