"""Антиспам: не больше N действий в секунду на пользователя.

Защищает и от двойных нажатий (двойная покупка), и от перебора кнопок.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.4):
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        user = data.get('event_from_user')
        if user:
            now = time.monotonic()
            if now - self._last.get(user.id, 0.0) < self.rate:
                if isinstance(event, types.CallbackQuery):
                    await event.answer()
                return None
            self._last[user.id] = now
        return await handler(event, data)
