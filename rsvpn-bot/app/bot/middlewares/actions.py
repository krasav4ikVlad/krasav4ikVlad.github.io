"""Строка в лог на каждое действие пользователя.

Без неё поддержка работать не может: человек пишет «я нажал продлить, и
списалось дважды», а в логах — только результат работы сервисов, без следа
самих нажатий. Восстановить, что он делал, нельзя.

Формат одной строкой, чтобы grep по id давал всю историю человека:

    → 802421217 @ivan  кнопка menu:my_subscription  (34 мс)
    → 802421217 @ivan  текст /start  (120 мс)
    → 802421217 @ivan  инлайн «1month»  (12 мс)
    ✖ 802421217 @ivan  кнопка plan:buy:1month  ОШИБКА ValueError  (56 мс)

Именно middleware, а не логирование внутри хендлеров: хендлеров четыре
десятка, и в каждом такая строка была бы копипастой, которую половина
забудет. Здесь одно место и никаких пропусков.

Что НЕ логируется — содержимое сообщений пользователя. В переписку с
поддержкой люди пишут почту и номера карт, и таким данным в логах, которые
читают операторы и хранит pm2, не место. Команды (/start, /admin) видны
целиком: в них личного нет.
"""

from __future__ import annotations

import logging
import time

from aiogram import BaseMiddleware, types

log = logging.getLogger('actions')

# Длина обрезки: длинный текст в логе мешает читать соседние строки.
MAX_TEXT = 64


def who(user: types.User | None) -> str:
    if user is None:
        return 'без пользователя'
    name = f' @{user.username}' if user.username else ''
    return f'{user.id}{name}'


def what(event) -> str:
    """Короткое описание действия — по нему оператор понимает, куда нажали."""
    if isinstance(event, types.CallbackQuery):
        return f'кнопка {event.data}'

    if isinstance(event, types.InlineQuery):
        return f'инлайн «{(event.query or "")[:MAX_TEXT]}»'

    if isinstance(event, types.Message):
        if event.text and event.text.startswith('/'):
            return f'команда {event.text[:MAX_TEXT]}'
        if event.successful_payment:
            return 'оплата через Telegram'
        if event.text:
            # только длина: в переписке бывают почта и реквизиты
            return f'текст ({len(event.text)} симв.)'
        return f'сообщение {event.content_type}'

    return type(event).__name__


class ActionLogMiddleware(BaseMiddleware):
    """Внешний middleware: ставится до фильтров, поэтому видит и то, что
    не дошло ни до одного хендлера."""

    async def __call__(self, handler, event, data):
        started = time.monotonic()
        user = data.get('event_from_user')
        try:
            result = await handler(event, data)
        except Exception as exc:
            log.warning('%s %s  ОШИБКА %s: %s  (%s мс)', who(user), what(event),
                        type(exc).__name__, exc, self._ms(started))
            raise

        log.info('%s %s  (%s мс)', who(user), what(event), self._ms(started))
        return result

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)
