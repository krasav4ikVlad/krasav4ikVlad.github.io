"""Экран = текст + клавиатура + картинка. Отрисовка одна на всех.

Это то, что убирает главный источник копипасты: в старом start.py каждый из
40 блоков сам собирал текст профиля и сам делал try/except с
edit_message_media. Здесь блок текста собирается из ключа в реестре, а
render() умеет и редактировать сообщение, и отправлять новое.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder


@dataclass(frozen=True)
class Screen:
    text: str
    markup: types.InlineKeyboardMarkup | None = None
    image: str | None = None


async def render(event: types.Message | types.CallbackQuery, screen: Screen):
    """Показать экран: правкой текущего сообщения (callback) или новым (message)."""
    if isinstance(event, types.CallbackQuery):
        message = event.message
        if screen.image:
            try:
                return await message.bot.edit_message_media(
                    chat_id=message.chat.id, message_id=message.message_id,
                    media=types.InputMediaPhoto(
                        media=types.FSInputFile(screen.image), caption=screen.text),
                    reply_markup=screen.markup)
            except Exception:
                pass
        try:
            if message.photo:
                return await message.edit_caption(caption=screen.text, reply_markup=screen.markup)
            return await message.edit_text(screen.text, reply_markup=screen.markup,
                                           disable_web_page_preview=True)
        except Exception:
            return await message.answer(screen.text, reply_markup=screen.markup,
                                        disable_web_page_preview=True)

    if screen.image:
        try:
            return await event.answer_photo(types.FSInputFile(screen.image),
                                            caption=screen.text, reply_markup=screen.markup)
        except Exception:
            pass
    return await event.answer(screen.text, reply_markup=screen.markup,
                              disable_web_page_preview=True)


def kb() -> InlineKeyboardBuilder:
    return InlineKeyboardBuilder()
