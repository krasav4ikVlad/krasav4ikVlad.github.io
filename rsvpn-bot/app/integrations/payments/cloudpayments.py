"""CloudPayments (карты РФ). Форма, id пользователя в AccountId."""

from __future__ import annotations

from app.integrations.payments.base import Invoice, PaymentProvider, WebhookEvent


class CloudPaymentsProvider(PaymentProvider):
    code = 'cards_ru'
    title = '💳 Карта РФ'
    verified = False   # ⚠️ HMAC из заголовка Content-HMAC сейчас не проверяется

    def __init__(self, public_id: str = '', secret: str = '', http=None):
        self._public_id = public_id
        self._secret = secret
        self._http = http

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        import base64
        from uuid import uuid4

        from app.core.errors import PaymentError

        auth = base64.b64encode(f'{self._public_id}:{self._secret}'.encode()).decode()
        data = await self._post(
            'https://api.cloudpayments.ru/orders/create',
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Basic {auth}'},
            json={'Amount': amount, 'Currency': 'RUB',
                  'Description': f'Пополнение баланса {user_id} на {amount}₽',
                  'InvoiceId': str(uuid4()), 'AccountId': str(user_id),
                  'SuccessRedirectUrl': self.success_url,
                  'FailRedirectUrl': self.success_url,
                  'SendEmail': False, 'SendSms': False, 'SendViber': False},
        )

        model = data.get('Model') or {}
        if not model.get('Url'):
            raise PaymentError(f'cloudpayments: {str(data)[:200]}')
        return Invoice(url=model['Url'], payment_id=str(model.get('Id') or ''),
                       amount=amount)

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
