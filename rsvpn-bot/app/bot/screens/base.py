"""Экран = текст + клавиатура + картинка. Отрисовка одна на всех.

Это то, что убирает главный источник копипасты: в старом start.py каждый из
40 блоков сам собирал текст профиля и сам делал try/except с
edit_message_media. Здесь блок текста собирается из ключа в реестре, а
render() умеет и редактировать сообщение, и отправлять новое.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder


@dataclass(frozen=True)
class Screen:
    text: str
    markup: types.InlineKeyboardMarkup | None = None
    # Photo из app/content/media.py: знает и путь к файлу, и его file_id
    image: Any = None


async def stop_spinner(event: types.CallbackQuery) -> None:
    """Погасить «часики» на кнопке до того, как соберётся экран.

    Telegram крутит индикатор, пока не придёт answerCallbackQuery, — то есть
    ровно всё время, что бот готовит и отправляет сообщение. Ответить сразу
    стоит один короткий запрос, зато нажатие ощущается мгновенным.

    Ошибки глушим: обработчик мог ответить сам (например, всплывающим
    текстом), и повторный ответ Telegram отклоняет — это не сбой сценария.
    """
    try:
        await event.answer()
    except Exception:
        pass


async def send_photo(send, photo, *, tries: int = 2):
    """Отправить картинку, а если Telegram не принял file_id — перезалить.

    file_id принадлежит конкретному боту и живёт не вечно: сменили токен —
    и все запомненные идентификаторы стали чужими, Telegram отвечает «wrong
    file identifier». Раньше это означало экран без картинки: ошибка гасла,
    и дальше шёл текстовый запасной путь. Теперь первый отказ стирает запись
    из кэша, и вторая попытка уходит с самим файлом.
    """
    for attempt in range(tries):
        cached = photo.cached()
        try:
            result = await send(photo.as_input())
            await photo.remember(result)
            return result
        except Exception:
            if not cached or attempt == tries - 1:
                raise
            await photo.forget()      # следующая попытка возьмёт файл с диска


def unchanged(exc: Exception) -> bool:
    """«message is not modified» — это не ошибка, а «и так уже правильно».

    Кнопка «Обновить» на экране, где ничего не поменялось, роняла все три
    попытки правки подряд, и последний запасной путь отправлял новое
    сообщение — текстом, без картинки. Экран задваивался ровно тогда, когда
    менять было нечего.
    """
    return 'not modified' in str(exc).lower()


async def render(event: types.Message | types.CallbackQuery, screen: Screen):
    """Показать экран: правкой текущего сообщения (callback) или новым (message)."""
    photo = screen.image

    if isinstance(event, types.CallbackQuery):
        await stop_spinner(event)
        message = event.message
        if photo:
            try:
                return await send_photo(lambda media: message.bot.edit_message_media(
                    chat_id=message.chat.id, message_id=message.message_id,
                    media=types.InputMediaPhoto(media=media, caption=screen.text),
                    reply_markup=screen.markup), photo)
            except Exception as exc:
                if unchanged(exc):
                    return None
        try:
            if message.photo:
                return await message.edit_caption(caption=screen.text, reply_markup=screen.markup)
            return await message.edit_text(screen.text, reply_markup=screen.markup,
                                           disable_web_page_preview=True)
        except Exception as exc:
            if unchanged(exc):
                return None
            return await message.answer(screen.text, reply_markup=screen.markup,
                                        disable_web_page_preview=True)

    if photo:
        try:
            return await send_photo(lambda media: event.answer_photo(
                media, caption=screen.text, reply_markup=screen.markup), photo)
        except Exception:
            pass
    return await event.answer(screen.text, reply_markup=screen.markup,
                              disable_web_page_preview=True)


def kb() -> InlineKeyboardBuilder:
    return InlineKeyboardBuilder()
