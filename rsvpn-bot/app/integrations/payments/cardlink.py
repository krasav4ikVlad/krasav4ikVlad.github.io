"""Cardlink. Пользователь определяется по счёту в cardlink_bills."""

from __future__ import annotations

import hashlib

from app.core import db as names
from app.integrations.payments.base import Invoice, PaymentProvider, WebhookEvent
from app.content.emoji import e


class CardlinkProvider(PaymentProvider):
    code = 'cardlink'
    title = f'{e("sbp")} СБП'
    verified = False   # ⚠️ Cardlink не подписывает вебхук — см. заметку в webhooks.py

    def __init__(self, token: str, shop_id: str = '', http=None, bills=None):
        self._token = token
        self._shop_id = shop_id
        self._http = http
        self._bills = bills      # коллекция cardlink_bills: по ней вебхук находит юзера

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        from uuid import uuid4

        from app.core.errors import PaymentError
        from app.core.time import now

        order_id = str(uuid4())
        data = await self._post(
            'https://cardlink.link/api/v1/bill/create',
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {self._token}'},
            json={'amount': amount, 'shop_id': self._shop_id, 'orderId': order_id,
                  'description': f'Пополнение баланса {user_id} на {amount}₽',
                  'successRedirectUrl': self.success_url,
                  'failRedirectUrl': self.success_url},
        )

        payment_id = str(data.get('bill_id') or '')
        url = data.get('link_page_url')
        if not payment_id or not url:
            raise PaymentError(f'cardlink: неполный ответ {str(data)[:200]}')

        # без этой записи вебхук не поймёт, кому зачислять
        if self._bills is not None:
            await self._bills.update_one(
                {'provider': 'cardlink', 'payment_id': payment_id},
                {'$setOnInsert': {'provider': 'cardlink', 'payment_id': payment_id,
                                  'order_id': order_id, 'user_id': int(user_id),
                                  'amount_rub': int(amount), 'status': 'created',
                                  'created_at': now(), 'raw_create': data}},
                upsert=True)

        return Invoice(url=url, payment_id=payment_id, amount=amount)

    def payment_signature(self, amount: str, trs_id: str) -> str:
        """Подпись для создания счёта (не для вебхука)."""
        return hashlib.md5(f'{amount}:{trs_id}:{self._token}'.encode()).hexdigest().upper()

    def parse(self, payload: dict) -> WebhookEvent:
        status = (payload.get('Status') or '').strip().upper()
        trs_id = (payload.get('TrsId') or '').strip()
        amount_raw = (payload.get('Amount') or payload.get('OutSum') or '').strip()

        if not trs_id or not amount_raw:
            return WebhookEvent.ignore('missing_fields')
        if status != 'SUCCESS':
            return WebhookEvent.ignore(f'status_{status.lower()}')

        try:
            amount = int(float(amount_raw))
        except ValueError:
            return WebhookEvent.ignore('bad_amount')

        return WebhookEvent(handled=True, txid=trs_id, user_id=None,
                            amount=amount, raw=payload)

    async def resolve_user(self, event: WebhookEvent, container) -> int | None:
        bill = await container.db[names.CARDLINK_BILLS].find_one(
            {'provider': 'cardlink', 'payment_id': event.txid})
        if not bill:
            return None
        await container.db[names.CARDLINK_BILLS].update_one(
            {'_id': bill['_id']},
            {'$set': {'status': 'success', 'credited_amount_rub': event.amount}})
        return int(bill['user_id'])
