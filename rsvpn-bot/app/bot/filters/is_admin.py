from __future__ import annotations

from aiogram import types
from aiogram.filters import Filter


class IsAdmin(Filter):
    def __init__(self, admin_ids: tuple[int, ...] | list[int]):
        self.admin_ids = set(admin_ids)

    async def __call__(self, event: types.TelegramObject) -> bool:
        user = getattr(event, 'from_user', None)
        return bool(user and user.id in self.admin_ids)
