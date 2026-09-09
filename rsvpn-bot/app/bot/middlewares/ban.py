"""Забаненные до хендлеров не доходят.

Стоит после UserMiddleware: документ уже прочитан, поэтому проверка не
стоит ни одного лишнего запроса.

Админов не блокирует никогда — иначе достаточно одной опечатки в id, чтобы
потерять доступ к собственной админке и не иметь возможности разбанить.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.services.moderation import ModerationService

log = logging.getLogger(__name__)


class BanMiddleware(BaseMiddleware):
    def __init__(self, settings, admin_ids):
        self.settings = settings
        self.admins = set(admin_ids)

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        tg_user = data.get('event_from_user')
        if tg_user and tg_user.id in self.admins:
            return await handler(event, data)

        if not ModerationService.is_banned(data.get('user')):
            return await handler(event, data)

        # Молчаливый режим: бот не отвечает вообще. Иначе — одна короткая
        # фраза, чтобы человек не считал, что бот сломался, и не шёл в
        # поддержку с вопросом «почему не работает».
        if not await self.settings.flag('moderation.ban_silent'):
            await self._reply(event, str(await self.settings.get('moderation.ban_message')))
        return None

    @staticmethod
    async def _reply(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, types.CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, types.Message):
                await event.answer(text)
        except Exception as exc:      # заблокировал бота — не наша забота
            log.debug('сообщение о блокировке не доставлено: %s', exc)
