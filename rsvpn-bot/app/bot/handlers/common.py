"""Мелочи, общие для всех разделов.

Пока здесь одна кнопка — «❌ Удалить сообщение». Она нужна везде, где бот
шлёт отдельное сообщение поверх экрана (ссылка подключения, инструкция,
предпросмотр): без неё чат зарастает, а закреплённый экран теряется.
"""

from __future__ import annotations

from aiogram import F, Router, types

from app.bot.callbacks import Menu
from app.content.emoji import e


def close_button(text: str = f'{e("cross")} Удалить сообщение') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Menu(screen='close').pack())


async def close(call: types.CallbackQuery) -> None:
    try:
        await call.message.delete()
    except Exception:
        # старше 48 часов Telegram удалять не даёт — тогда просто убираем кнопки
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await call.answer()


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='common')
    router.callback_query.register(close, Menu.filter(F.screen == 'close'))
    return router
