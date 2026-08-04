"""Ошибки показываем пользователю по-человечески, а в лог — со стеком."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.core.errors import AppError

log = logging.getLogger(__name__)


class ErrorsMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        try:
            return await handler(event, data)
        except AppError as exc:
            log.info('ожидаемая ошибка: %s', exc)
            await self._reply(event, exc.user_message)
        except Exception:
            log.exception('необработанная ошибка в хендлере')
            await self._reply(event, AppError.user_message)
        return None

    @staticmethod
    async def _reply(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, types.CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, types.Message):
                await event.answer(text)
        except Exception:  # noqa: BLE001
            pass
