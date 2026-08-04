"""Реестр провайдеров: включение/выключение и поиск по коду."""

from __future__ import annotations

from app.integrations.payments.base import PaymentProvider


class PaymentRegistry:
    def __init__(self, providers: list[PaymentProvider], settings):
        self._providers = {p.code: p for p in providers}
        self._settings = settings

    def get(self, code: str) -> PaymentProvider | None:
        return self._providers.get(code)

    async def available(self) -> list[PaymentProvider]:
        """Провайдеры, включённые тумблером в админке (pay.<code>_enabled)."""
        result = []
        for code, provider in self._providers.items():
            key = f'pay.{code}_enabled'
            if key not in self._settings.index or await self._settings.flag(key):
                result.append(provider)
        return result
