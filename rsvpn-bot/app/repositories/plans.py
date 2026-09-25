"""Тарифы: документы вместо if-цепочек, с кэшем на несколько секунд."""

from __future__ import annotations

import time

from app.core.time import now
from app.repositories.base import Repository

CACHE_TTL_SEC = 10.0

DEFAULT_PLANS = [
    {'code': '1day', 'title': 'Ежедневная', 'days': 1, 'price': 6, 'order': 10,
     'gift_type': '', 'gift_count': 0, 'enabled': True},
    {'code': '1month', 'title': '1 месяц', 'days': 30, 'price': 150, 'order': 20,
     'gift_type': '1day', 'gift_count': 1, 'enabled': True},
    {'code': '3month', 'title': '3 месяца', 'days': 90, 'price': 375, 'order': 30,
     'gift_type': '1month', 'gift_count': 1, 'enabled': True},
    {'code': '3year', 'title': '3 года', 'days': 1095, 'price': 3000, 'order': 40,
     'gift_type': '1month', 'gift_count': 3, 'enabled': True},
]


class PlansRepository(Repository):
    def __init__(self, collection):
        super().__init__(collection)
        self._cache: list[dict] = []
        self._loaded_at = 0.0

    async def ensure_indexes(self) -> None:
        await self.ensure_index('code', unique=True)

    async def seed(self) -> None:
        if await self.col.count_documents({}) == 0:
            await self.col.insert_many([dict(p, created_at=now()) for p in DEFAULT_PLANS])
        self.invalidate()

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def _load(self) -> list[dict]:
        if self._cache and (time.monotonic() - self._loaded_at) < CACHE_TTL_SEC:
            return self._cache
        self._cache = await self.col.find({}, {'_id': 0}).sort('order', 1).to_list(length=200)
        self._loaded_at = time.monotonic()
        return self._cache

    async def all(self, only_enabled: bool = True) -> list[dict]:
        plans = await self._load()
        return [p for p in plans if p.get('enabled', True)] if only_enabled else list(plans)

    async def get(self, code: str) -> dict | None:
        return next((p for p in await self._load() if p.get('code') == code), None)

    async def shortest(self) -> dict | None:
        """Самый короткий включённый тариф.

        Нужен там, где длительность нужна, а человек её не выбирал: после
        бесплатного периода и при починке подписки со сроком, которого нет
        среди тарифов. Брать самый короткий — самое щадящее решение: списание
        минимальное, и человек успевает решить сам.
        """
        plans = await self.all()
        return min(plans, key=lambda p: int(p.get('days', 0) or 0), default=None)

    async def by_days(self, days: int) -> dict | None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return None
        return next((p for p in await self._load() if int(p.get('days', 0)) == days), None)
