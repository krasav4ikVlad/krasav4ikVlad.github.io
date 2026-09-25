"""Ошибки показываем пользователю по-человечески, а в лог — со стеком.

И записываем: «сервис подписок не отвечает» — честный текст для человека и
бесполезный для того, кто чинит. Кто именно, на каком экране и что на самом
деле ответила панель, видно только в журнале ошибок (/errors).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.core.errors import AppError, VpnPanelError

log = logging.getLogger(__name__)

# Потолок на одно нажатие кнопки. Нужен ровно для одного случая: база не
# отвечает, и каждый запрос к ней висит до своего таймаута. Экран делает их
# десяток, и человек ждёт минуты — а Telegram к тому времени уже отвечает
# «query is too old», то есть нажатие пропадает совсем. Лучше честное
# «не дождались» через полминуты.
#
# Только на кнопки: админские отчёты (/raffle по всему журналу, /money за
# два месяца) идут командами и законно считаются дольше.
CALLBACK_TIMEOUT_SEC = 30

DB_DOWN = ('База сейчас не отвечает — мы уже знаем и чиним. '
           'Попробуйте через пару минут, подписка при этом работает.')
TOO_SLOW = ('Не дождались ответа базы. Попробуйте ещё раз через минуту — '
            'на подписку это не влияет.')


def is_db_error(exc: BaseException) -> bool:
    """Ошибка Mongo — по имени модуля, а не по импорту pymongo.

    Импортировать драйвер здесь незачем: middleware должен читаться и в
    тестах, где его нет, а имя модуля у исключений драйвера всегда своё.
    """
    module = type(exc).__module__ or ''
    return module.startswith(('pymongo', 'motor'))


class ErrorsMiddleware(BaseMiddleware):
    def __init__(self, container=None):
        # Контейнер, а не data['c']: этот middleware стоит раньше того,
        # который кладёт зависимости, — иначе он не поймал бы ошибки в нём
        # самом.
        self.container = container

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        try:
            if isinstance(event, types.CallbackQuery):
                return await asyncio.wait_for(handler(event, data),
                                              timeout=CALLBACK_TIMEOUT_SEC)
            return await handler(event, data)
        except asyncio.TimeoutError:
            log.error('нажатие не уложилось в %sс: %s', CALLBACK_TIMEOUT_SEC,
                      getattr(event, 'data', ''))
            await self._reply(event, TOO_SLOW)
        except AppError as exc:
            log.info('ожидаемая ошибка: %s', exc)
            await self._save(event, exc, exc.user_message,
                             'panel' if isinstance(exc, VpnPanelError) else 'app')
            await self._reply(event, exc.user_message)
        except Exception as exc:
            if is_db_error(exc):
                # Про базу отдельно: писать «что-то пошло не так» и молча
                # пытаться сохранить это В ТУ ЖЕ базу — худший из ответов.
                log.error('база не отвечает: %s', str(exc)[:200])
                await self._reply(event, DB_DOWN)
                return None

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
