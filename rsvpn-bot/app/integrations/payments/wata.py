"""WATA. Сумма приходит с комиссией сверху, id пользователя — в описании заказа."""

from __future__ import annotations

import re

from app.integrations.payments.base import PaymentProvider, WebhookEvent

UID_IN_DESCRIPTION = re.compile(r'Пополнение\s+баланса\s+(\d+)', re.IGNORECASE)


class WataProvider(PaymentProvider):
    code = 'wata'
    title = '⚡️ СБП'
    min_amount = 100
    verified = False   # ⚠️ подписи в текущем обработчике нет — см. webhooks.py

    def __init__(self, token: str, token_visa: str = '', fee_rate: float = 0.05, http=None):
        self._token = token
        self._token_visa = token_visa
        self._fee_rate = fee_rate
        self._http = http

    def parse(self, payload: dict) -> WebhookEvent:
        if payload.get('transactionStatus') != 'Paid':
            return WebhookEvent.ignore('not_paid')

        try:
            paid = float(payload.get('amount', 0))
        except (TypeError, ValueError):
            return WebhookEvent.ignore('bad_amount')

        # зачисляем без комиссии шлюза: оплатил 105 → на баланс 100
        amount = int(round(paid / (1 + self._fee_rate)))

        match = UID_IN_DESCRIPTION.search(payload.get('orderDescription', '') or '')
        user_id = int(match.group(1)) if match else None

        if not user_id or amount <= 0:
            return WebhookEvent.ignore('no_user_or_amount')

        txid = payload.get('transactionId') or payload.get('paymentLinkId') or ''
        return WebhookEvent(handled=True, txid=str(txid), user_id=user_id,
                            amount=amount, raw=payload)
