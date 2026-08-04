"""Общий контракт платёжных провайдеров.

В старом FastApi.py шесть эндпоинтов повторяют одно и то же: распарсить,
проверить подпись, достать user_id и сумму, вызвать пополнение. Отличия —
только внутри этих четырёх шагов. Здесь они вынесены в интерфейс, а
эндпоинт остаётся один (см. app/api/webhooks.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Invoice:
    """Счёт на оплату, который показываем пользователю."""
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
    """Подпись вебхука не сошлась — запрос отклоняем с 403."""


class PaymentProvider(Protocol):
    code: str
    title: str
    min_amount: int

    async def create_invoice(self, user_id: int, amount: int) -> Invoice:
        """Создать счёт. Ошибки провайдера — PaymentError из app.core.errors."""

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        """Проверить подпись. Бросает SignatureError, если не сошлась."""

    def parse(self, payload: dict) -> WebhookEvent:
        """Достать из вебхука user_id, сумму и идемпотентный txid."""
