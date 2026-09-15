"""Загрузка пользователя один раз за апдейт.

Сейчас каждый хендлер сам делает users.find_one, а некоторые — по два раза
за один экран. Здесь документ читается один раз и кладётся в data['user'].
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class UserMiddleware(BaseMiddleware):
    def __init__(self, users_repo, projection: dict | None = None):
        self.users = users_repo
        self.projection = projection or {'logs': 0}

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        tg_user = data.get('event_from_user')
        if tg_user:
            data['user'] = await self.users.get(tg_user.id, self.projection)
        return await handler(event, data)
