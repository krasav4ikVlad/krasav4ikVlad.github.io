"""Ошибки показываем пользователю по-человечески, а в лог — со стеком.

И записываем: «сервис подписок не отвечает» — честный текст для человека и
бесполезный для того, кто чинит. Кто именно, на каком экране и что на самом
деле ответила панель, видно только в журнале ошибок (/errors).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.core.errors import AppError, VpnPanelError

log = logging.getLogger(__name__)


class ErrorsMiddleware(BaseMiddleware):
    def __init__(self, container=None):
        # Контейнер, а не data['c']: этот middleware стоит раньше того,
        # который кладёт зависимости, — иначе он не поймал бы ошибки в нём
        # самом.
        self.container = container

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        try:
            return await handler(event, data)
        except AppError as exc:
            log.info('ожидаемая ошибка: %s', exc)
            await self._save(event, exc, exc.user_message,
                             'panel' if isinstance(exc, VpnPanelError) else 'app')
            await self._reply(event, exc.user_message)
        except Exception as exc:
            log.exception('необработанная ошибка в хендлере')
            await self._save(event, exc, AppError.user_message, 'crash')
            await self._reply(event, AppError.user_message)
        return None

    async def _save(self, event: TelegramObject, exc: Exception,
                    shown: str, kind: str) -> None:
        journal = getattr(self.container, 'errors', None)
        if journal is None:
            return

        user = getattr(event, 'from_user', None)
        where = ''
        if isinstance(event, types.CallbackQuery):
            where = event.data or ''
        elif isinstance(event, types.Message):
            # Текст сообщения не пишем: люди присылают в поддержку почту и
            # номера карт, и журнал ошибок — последнее место, где им лежать.
            # Команда — другое дело, по ней и ищут.
            text = (event.text or '')
            where = text.split()[0] if text.startswith('/') else 'сообщение'

        try:
            await journal.record(
                user_id=getattr(user, 'id', 0) or 0,
                username=getattr(user, 'username', '') or '',
                kind=kind, error=type(exc).__name__, message=str(exc),
                where=where, shown=shown)
        except Exception:      # noqa: BLE001 — журнал не должен мешать ответу
            pass

    @staticmethod
    async def _reply(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, types.CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, types.Message):
                await event.answer(text)
        except Exception:  # noqa: BLE001
            pass
