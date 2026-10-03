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

from app.content.emoji import plain

log = logging.getLogger(__name__)


# Сколько в сумме готовы ждать флуд-паузу на ОДНОМ письме. Telegram при
# большой рассылке отвечает 429 и просит подождать; обычно это секунды, но
# на десятках тысяч писем попадаются паузы в десятки минут. Ждать их молча
# нельзя — со стороны это неотличимо от зависшей рассылки.
MAX_FLOOD_WAIT_SEC = 300


class Sender:
    def __init__(self, max_retries: int = 3, on_blocked=None, on_flood=None):
        self.max_retries = max_retries
        self.on_blocked = on_blocked  # колбэк: пометить пользователя заблокировавшим
        # колбэк: сообщить наверх, что идёт вынужденная пауза и сколько секунд
        self.on_flood = on_flood

    async def send(self, bot: Bot, user_id: int, text: str, markup=None) -> bool:
        # Рассылка из админки запускается фоновой задачей прямо из хендлера, а
        # задача забирает с собой его контекст — вместе с «обычными значками»
        # админки. Письмо уходит пользователю, поэтому значки здесь обычные.
        with plain(False):
            return await self._send(bot, user_id, text, markup)

    async def _send(self, bot: Bot, user_id: int, text: str, markup=None) -> bool:
        waited = 0
        attempt = 0
        while attempt < self.max_retries:
            try:
                await bot.send_message(user_id, text, reply_markup=markup)
                return True
            except TelegramRetryAfter as exc:
                # Пауза по требованию Telegram — не наша ошибка и не отказ
                # адресата, поэтому попытку она не тратит. Раньше три подряд
                # флуд-паузы записывали живого человека в «не доставлено».
                wait = int(exc.retry_after) + 1
                log.warning('флуд-лимит: ждём %sс (адресат %s)', wait, user_id)
                if self.on_flood:
                    await self.on_flood(wait)
                if waited + wait > MAX_FLOOD_WAIT_SEC:
                    log.error('флуд-пауза больше %sс — письмо %s отложено',
                              MAX_FLOOD_WAIT_SEC, user_id)
                    return False
                waited += wait
                await asyncio.sleep(wait)
                continue
            except TelegramForbiddenError:
                # пользователь заблокировал бота — это не ошибка, это факт
                if self.on_blocked:
                    await self.on_blocked(user_id)
                return False
            except TelegramBadRequest as exc:
                log.warning('не доставлено %s: %s', user_id, exc.message)
                return False
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                log.warning('ошибка отправки %s (попытка %s): %s', user_id, attempt, exc)
                await asyncio.sleep(1)
        return False
