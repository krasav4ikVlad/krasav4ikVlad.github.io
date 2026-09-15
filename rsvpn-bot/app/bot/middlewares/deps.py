"""Проброс зависимостей в хендлеры.

Вместо `from loader import users, bot` в каждом файле — контейнер приезжает
аргументом. Это то, что позволяет запускать хендлеры в тестах на заглушках.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class DependenciesMiddleware(BaseMiddleware):
    def __init__(self, container):
        self.container = container

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        data['c'] = self.container
        data['settings'] = self.container.settings
        return await handler(event, data)
