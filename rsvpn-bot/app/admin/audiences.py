"""Сколько людей в каждой аудитории.

Одно место на всю админку: скидки, рассылка, сброс триала спрашивают числа
здесь и потому не могут разойтись между собой.

Считается по `growth.segment` — полю, которое пересчитывает планировщик раз
в час. Значит, числа отстают: человек, у которого подписка кончилась пять
минут назад, попадёт в «без активной» после ближайшего пересчёта. Для
выбора аудитории этого достаточно, а вот выдавать их за живую статистику
нельзя — для неё есть admin/stats.py.

Кэш на полминуты: экран настроек открывают подряд по нескольку раз, и
каждое открытие иначе стоило бы семь запросов в базу.
"""

from __future__ import annotations

import logging
import time

from app.domain.segments import AUDIENCES, audience_query

log = logging.getLogger(__name__)

CACHE_TTL_SEC = 30.0


class AudienceCounts:
    def __init__(self, users, ttl: float = CACHE_TTL_SEC):
        self.users = users
        self.ttl = ttl
        self._cache: dict[str, int] = {}
        self._loaded_at = 0.0

    async def all(self, force: bool = False) -> dict[str, int]:
        """Код аудитории → сколько человек. Отказ базы — пустой словарь.

        Пустой, а не нули: «не смогли посчитать» и «никого нет» на экране
        должны выглядеть по-разному, иначе рассылку отменят из-за мнимого
        нуля получателей.
        """
        if self._cache and not force and (time.monotonic() - self._loaded_at) < self.ttl:
            return self._cache

        counts: dict[str, int] = {}
        try:
            for code in AUDIENCES:
                counts[code] = await self.users.col.count_documents(audience_query(code))
        except Exception as exc:
            log.warning('аудитории не посчитаны: %s', exc)
            return {}

        self._cache = counts
        self._loaded_at = time.monotonic()
        return counts

    async def get(self, code: str) -> int:
        return (await self.all()).get(code, 0)

    def invalidate(self) -> None:
        self._loaded_at = 0.0
