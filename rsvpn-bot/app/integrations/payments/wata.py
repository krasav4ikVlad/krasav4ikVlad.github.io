"""WATA. Сумма приходит с комиссией сверху, id пользователя — в описании заказа."""

from __future__ import annotations

import re

from app.integrations.payments.base import Invoice, PaymentProvider, WebhookEvent
from app.content.emoji import e

UID_IN_DESCRIPTION = re.compile(r'Пополнение\s+баланса\s+(\d+)', re.IGNORECASE)


class WataProvider(PaymentProvider):
    code = 'wata'
    title = f'{e("sbp")} СБП'
    min_amount = 100
    verified = False   # ⚠️ подписи в текущем обработчике нет — см. webhooks.py

    def __init__(self, token: str, token_visa: str = '', fee_rate: float = 0.05, http=None):
        self._token = token
        self._token_visa = token_visa
        self._fee_rate = fee_rate
        self._http = http

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        from uuid import uuid4

        from app.core.errors import PaymentError

        # сумма к оплате с учётом комиссии: на баланс придёт ровно amount
        to_pay = int(round(amount * (1 + self._fee_rate)))

        data = await self._post(
            'https://api.wata.pro/api/h2h/links',
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {self._token}'},
            json={'amount': to_pay, 'currency': 'RUB',
                  'description': f'Пополнение баланса {user_id} на {amount}₽',
                  'orderId': str(uuid4()),
                  'successRedirectUrl': self.success_url,
                  'failRedirectUrl': self.success_url},
        )

        url = data.get('url')
        if not url:
            raise PaymentError(f'wata: нет ссылки в ответе {str(data)[:200]}')
        return Invoice(url=url, payment_id=str(data.get('id') or ''), amount=to_pay)

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
