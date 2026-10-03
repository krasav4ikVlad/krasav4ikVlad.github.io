"""Фильтр «функция включена в админке».

    @router.callback_query(Menu.filter(F.screen == 'extend'), Feature('features.extend_enabled'))

Если тумблер выключен — хендлер не сработает, а пользователь увидит alert.
Одна строка вместо проверки внутри каждого хендлера.
"""

from __future__ import annotations

from aiogram import types
from aiogram.filters import Filter


class Feature(Filter):
    def __init__(self, key: str, alert: str = 'Функция временно отключена'):
        self.key = key
        self.alert = alert

    async def __call__(self, event: types.TelegramObject, settings=None, **_) -> bool:
        if settings is None or await settings.flag(self.key):
            return True
        if isinstance(event, types.CallbackQuery):
            await event.answer(self.alert, show_alert=True)
        return False
