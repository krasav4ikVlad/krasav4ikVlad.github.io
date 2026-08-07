"""Кастомные эмодзи навешиваются на выходе из бота.

Единственная точка, через которую проходит ВСЁ, что бот отправляет в
Telegram, — сессия. Здесь у уже собранного запроса подменяются значки, и
для каждого места своим способом, потому что способов ровно три:

* текст и подпись — тег <tg-emoji> в HTML;
* подпись кнопки — HTML там не разбирается, зато у InlineKeyboardButton
  есть поле icon_custom_emoji_id: значок уезжает туда, а из подписи
  убирается;
* всплывающий ответ на нажатие (answerCallbackQuery) — не поддерживает
  ни того, ни другого. Его не трогаем вообще, иначе человек увидит
  «<tg-emoji emoji-id="…">✅</tg-emoji>» буквами.

Отличить первое от третьего можно надёжно: HTML разбирается только там,
где у метода есть parse_mode. У answerCallbackQuery его нет.
"""

from __future__ import annotations

import logging

from app.content.emoji import decorate, enabled, leading_emoji_id

log = logging.getLogger(__name__)

TEXT_FIELDS = ('text', 'caption')


def decorate_buttons(markup) -> None:
    """Значок из начала подписи → в icon_custom_emoji_id.

    Telegram рисует иконку слева от текста, поэтому символ из подписи
    убирается: иначе он покажется дважды.
    """
    rows = getattr(markup, 'inline_keyboard', None)
    if not rows:
        return

    for row in rows:
        for button in row:
            if getattr(button, 'icon_custom_emoji_id', None):
                continue        # проставлено вручную — не переигрываем

            emoji_id, rest = leading_emoji_id(getattr(button, 'text', '') or '')
            if not emoji_id:
                continue
            button.icon_custom_emoji_id = emoji_id
            button.text = rest


async def emoji_middleware(make_request, bot, method):
    """Session middleware aiogram: bot.session.middleware(emoji_middleware)."""
    if not enabled():
        return await make_request(bot, method)

    try:
        # текст: только там, где Telegram вообще разбирает HTML
        if 'parse_mode' in type(method).model_fields:
            for field in TEXT_FIELDS:
                value = getattr(method, field, None)
                if isinstance(value, str) and value:
                    setattr(method, field, decorate(value))

        # подпись у фото при edit_message_media лежит внутри media,
        # а InputMedia* заморожен — только копией
        media = getattr(method, 'media', None)
        caption = getattr(media, 'caption', None)
        if isinstance(caption, str) and caption:
            method.media = media.model_copy(
                update={'caption': decorate(caption)})

        decorate_buttons(getattr(method, 'reply_markup', None))
    except Exception as exc:      # оформление не должно мешать отправке
        log.warning('эмодзи не подставлены: %s', exc, exc_info=True)

    return await make_request(bot, method)
