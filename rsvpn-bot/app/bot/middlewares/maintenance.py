"""Режим техработ: тумблер features.maintenance_mode. Админов не касается.

Раньше здесь был всплывающий ответ на нажатие — тот, что показывается
поверх экрана и исчезает. Он не говорил ни что случилось, ни надолго ли, и
повторялся на каждое нажатие, пока человек не бросал. Теперь показывается
нормальный экран, и он же остаётся на месте прежнего.

Одно исключение — сапёр: во время техработ это единственное, что человеку
разрешено, и пропускать его обязан именно перехватчик. Игра не трогает ни
панель, ни баланс, поэтому «ничего нельзя» она не нарушает.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.bot.callbacks import Game

GAME_PREFIX = f'{Game.__prefix__}{Game.__separator__}'


def is_game(event: TelegramObject) -> bool:
    return (isinstance(event, types.CallbackQuery)
            and (event.data or '').startswith(GAME_PREFIX))


class MaintenanceMiddleware(BaseMiddleware):
    def __init__(self, settings, admin_ids: tuple[int, ...] | list[int]):
        self.settings = settings
        self.admin_ids = set(admin_ids)

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        user = data.get('event_from_user')
        if user and user.id in self.admin_ids:
            return await handler(event, data)
        if not await self.settings.flag('features.maintenance_mode'):
            return await handler(event, data)

        if is_game(event) and await self.settings.flag('features.maintenance_game'):
            return await handler(event, data)

        # Инлайн-режим отвечать текстом не умеет: там нельзя ни написать
        # человеку, ни показать экран — только отдать список вариантов.
        # Пустой список и есть честный ответ «сейчас ничего нет».
        if isinstance(event, types.InlineQuery):
            try:
                await event.answer([], cache_time=1, is_personal=True)
            except Exception:
                pass
            return None

        from app.bot.handlers.maintenance import show_maintenance

        await show_maintenance(event, self.settings)
        return None
