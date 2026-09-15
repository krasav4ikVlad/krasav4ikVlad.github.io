"""Фильтры и middleware, которые включают/выключают функции бота из админки.

Вместо «закомментировать кнопку и задеплоить» — переключатель в /admin.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.filters import Filter

from loader import admins_ids
from core.settings import S


class Feature(Filter):
    """Хендлер срабатывает, только если функция включена в админке.

    @router.callback_query(F.data == 'menu:extend', Feature('features.extend_enabled'))
    async def extend(...): ...
    """

    def __init__(self, key: str, alert: str = 'Функция временно отключена'):
        self.key = key
        self.alert = alert

    async def __call__(self, event: types.TelegramObject) -> bool:
        if await S.flag(self.key):
            return True
        if isinstance(event, types.CallbackQuery):
            await event.answer(self.alert, show_alert=True)
        return False


class IsAdmin(Filter):
    async def __call__(self, event: types.TelegramObject) -> bool:
        user = getattr(event, 'from_user', None)
        return bool(user and user.id in admins_ids)


class MaintenanceMiddleware(BaseMiddleware):
    """Режим техработ: включается тумблером features.maintenance_mode.

    Админы продолжают пользоваться ботом как обычно.
    """

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, 'from_user', None)
        if user and user.id in admins_ids:
            return await handler(event, data)

        if not await S.flag('features.maintenance_mode'):
            return await handler(event, data)

        text = await S.get('text.maintenance')
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, types.Message):
            await event.answer(text)
        return None
