"""Режим техработ: тумблер features.maintenance_mode. Админов не касается."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject


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

        text = await self.settings.get('text.maintenance')
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, types.Message):
            await event.answer(text)
        return None
