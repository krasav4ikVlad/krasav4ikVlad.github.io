"""Cardlink. Пример того, как выглядит провайдер целиком."""

from __future__ import annotations

import hashlib

from app.integrations.payments.base import Invoice, PaymentProvider, WebhookEvent


class CardlinkProvider(PaymentProvider):
    code = 'cardlink'
    title = '💳 Карта РФ'
    min_amount = 75

    def __init__(self, token: str, shop_id: str = '', http=None, bills=None):
        self._token = token
        self._shop_id = shop_id
        self._http = http      # общий httpx.AsyncClient из контейнера
        self._bills = bills    # коллекция для сопоставления payment_id → user_id

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        # TODO: перенести сюда вызов API из utility/utils.create_payment_cardlink
        raise NotImplementedError

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        # Cardlink подписывает не тело, а поля; проверка — в parse()
        return None

    def signature(self, amount: str, trs_id: str) -> str:
        raw = f'{amount}:{trs_id}:{self._token}'
        return hashlib.md5(raw.encode()).hexdigest().upper()

    def parse(self, payload: dict) -> WebhookEvent:
        status = (payload.get('Status') or '').strip().upper()
        trs_id = (payload.get('TrsId') or '').strip()
        amount = (payload.get('Amount') or payload.get('OutSum') or '').strip()

        if not trs_id or not amount:
            return WebhookEvent.ignore('missing_fields')
        if status != 'SUCCESS':
            return WebhookEvent.ignore(f'status_{status.lower()}')

        return WebhookEvent(
            handled=True,
            txid=trs_id,
            user_id=None,          # у Cardlink id пользователя берётся из счёта в БД
            amount=int(float(amount)),
            raw=payload,
        )
