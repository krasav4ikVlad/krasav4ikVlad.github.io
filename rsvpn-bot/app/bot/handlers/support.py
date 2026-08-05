"""Поддержка и статические экраны."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render


async def about(event, settings, c=None):
    offer = await settings.get('link.offer')
    privacy = await settings.get('link.privacy')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text='📄 Публичная оферта', url=offer))
    kb.row(types.InlineKeyboardButton(text='🔒 Политика конфиденциальности', url=privacy))
    await footer(kb, settings, back='profile')

    await render(event, Screen(
        text=('<b>ℹ️ О сервисе</b>\n\nRS VPN — доступ к интернету без ограничений.\n'
              'Оплата с баланса, продление автоматическое.'),
        markup=kb.as_markup()))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def about_button(message: types.Message, settings):
    await about(message, settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='support')
    router.message.register(about, Command('privacy'))
    router.callback_query.register(about, Menu.filter(F.screen == 'about'))
    router.message.register(about_button, F.text.in_({'О сервисе', 'ℹ️ О сервисе'}))
    return router
