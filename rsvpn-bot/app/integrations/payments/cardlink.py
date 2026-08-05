"""Cardlink. Пользователь определяется по счёту в cardlink_bills."""

from __future__ import annotations

import hashlib

from app.core import db as names
from app.integrations.payments.base import PaymentProvider, WebhookEvent


class CardlinkProvider(PaymentProvider):
    code = 'cardlink'
    title = '💳 Карта РФ'
    verified = False   # ⚠️ Cardlink не подписывает вебхук — см. заметку в webhooks.py

    def __init__(self, token: str, shop_id: str = '', http=None):
        self._token = token
        self._shop_id = shop_id
        self._http = http

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
