"""Кастомные эмодзи навешиваются на выходе из бота.

Единственная точка, через которую проходит ВСЁ, что бот отправляет в
Telegram, — сессия. Здесь у уже собранного запроса подменяются известные
символы в `text` и `caption`.

Почему не в коде экранов: разметка работает в тексте сообщения, но не в
подписи кнопки — там тег показался бы буквами. Подписи лежат в reply_markup,
и эта middleware их не трогает по построению. То есть перепутать место
больше нельзя: в коде везде `e("имя")`, а где это станет кастомным эмодзи,
решается здесь и одинаково.
"""

from __future__ import annotations

import logging

from app.content.emoji import decorate

log = logging.getLogger(__name__)

# Поля, в которых Telegram разбирает HTML. inline-результаты трогаем через
# input_message_content — у них своя вложенность.
TEXT_FIELDS = ('text', 'caption')


async def emoji_middleware(make_request, bot, method):
    """Session middleware aiogram: bot.session.middleware(emoji_middleware)."""
    try:
        for field in TEXT_FIELDS:
            value = getattr(method, field, None)
            if isinstance(value, str) and value:
                setattr(method, field, decorate(value))
    except Exception as exc:      # оформление не должно мешать отправке
        log.warning('эмодзи не подставлены: %s', exc)

    return await make_request(bot, method)
