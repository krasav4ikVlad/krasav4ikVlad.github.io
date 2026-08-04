"""Отправка сообщений пачкой: флуд-контроль, блокировки, метрики.

Одна реализация на кампании и на ручные рассылки из админки. В старом коде
safe_send скопирован в трёх файлах, а рассылка в admin.py вообще шлёт без
пауз и молча теряет пользователей в `except: failed += 1`.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)

log = logging.getLogger(__name__)


class Sender:
    def __init__(self, max_retries: int = 3, on_blocked=None):
        self.max_retries = max_retries
        self.on_blocked = on_blocked  # колбэк: пометить пользователя заблокировавшим

    async def send(self, bot: Bot, user_id: int, text: str, markup=None) -> bool:
        for attempt in range(self.max_retries):
            try:
                await bot.send_message(user_id, text, reply_markup=markup)
                return True
            except TelegramRetryAfter as exc:
                log.warning('флуд-лимит: ждём %sс', exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramForbiddenError:
                # пользователь заблокировал бота — это не ошибка, это факт
                if self.on_blocked:
                    await self.on_blocked(user_id)
                return False
            except TelegramBadRequest as exc:
                log.warning('не доставлено %s: %s', user_id, exc.message)
                return False
            except Exception as exc:  # noqa: BLE001
                log.warning('ошибка отправки %s (попытка %s): %s', user_id, attempt + 1, exc)
                await asyncio.sleep(1)
        return False
