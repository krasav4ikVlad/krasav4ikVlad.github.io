"""Общий контракт платёжных провайдеров.

В FastApi.py шесть эндпоинтов повторяют одно и то же: распарсить тело,
проверить подпись, достать user_id и сумму, зачислить. Отличаются только эти
четыре шага — они и вынесены в интерфейс, а эндпоинт остаётся один
(app/api/webhooks.py).

Отдельное поле verified показывает, проверяется ли подпись вообще. Сейчас у
WATA и Cardlink проверки нет: кто угодно, зная адрес вебхука, может отправить
«оплату». Здесь это хотя бы видно в коде, а не растворено в 900 строках.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Invoice:
    url: str
    payment_id: str
    amount: int


@dataclass(frozen=True)
class WebhookEvent:
    """Разобранный вебхук. handled=False — событие не про оплату, игнорируем."""
    handled: bool
    txid: str = ''
    user_id: int | None = None
    amount: int = 0
    note: str = ''
    raw: dict = field(default_factory=dict)

    @classmethod
    def ignore(cls, note: str) -> 'WebhookEvent':
        return cls(handled=False, note=note)


class SignatureError(Exception):
    """Подпись вебхука не сошлась — запрос отклоняем."""


class PaymentProvider:
    code: str = ''
    title: str = ''
    min_amount: int = 75
    verified: bool = True      # проверяется ли подпись входящего вебхука

    # Готовая ссылка на оплату (мини-апп Tribute). Если задана, бот не
    # спрашивает сумму и не выставляет счёт: кнопка ведёт прямо в интерфейс
    # провайдера, где человек сам выбирает сумму, а мы узнаём её из вебхука.
    direct_url: str = ''

    # общие настройки, проставляются реестром при сборке
    success_url: str = 'https://t.me/rsconnect_bot'
    callback_base: str = 'https://webhook.rsvps.tech'

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        raise NotImplementedError

    async def _post(self, url: str, *, headers=None, json=None, content=None) -> dict:
        """POST с разбором ответа. Ошибку превращает в PaymentError с текстом."""
        from app.core.errors import PaymentError

        if self._http is None:
            raise PaymentError('нет http-клиента')

        try:
            response = await self._http.post(url, headers=headers, json=json,
                                             content=content, timeout=30)
        except Exception as exc:
            raise PaymentError(f'{self.code}: {exc}') from exc

        if response.status_code != 200:
            raise PaymentError(f'{self.code}: HTTP {response.status_code} '
                               f'{response.text[:200]}')
        try:
            return response.json()
        except Exception as exc:
            raise PaymentError(f'{self.code}: ответ не JSON') from exc

    def verify(self, body: bytes, headers: dict[str, str], payload: dict) -> None:
        """Бросает SignatureError, если подпись не сошлась."""

    def parse(self, payload: dict) -> WebhookEvent:
        raise NotImplementedError

    async def resolve_user(self, event: WebhookEvent, container) -> int | None:
        """Кому зачислять. Переопределяется, если id берётся не из вебхука."""
        return event.user_id
