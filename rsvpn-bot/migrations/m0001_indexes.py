"""Индексы и первичное наполнение справочников.

strict=True: если индекс создать не удалось, миграция падает и НЕ помечается
выполненной — иначе повторный запуск сказал бы «уже применена», а индекса
так и не было бы.
"""

from __future__ import annotations


async def up(container) -> None:
    await container.startup(strict=True)
