"""Экран профиля."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption


async def profile_keyboard(user: dict, settings) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    has_sub = bool((user.get('vpn') or {}).get('shortUuid'))

    kb.row(types.InlineKeyboardButton(
        text='🛡 Ваша подписка' if has_sub else '➕ Подключить RS VPN',
        callback_data=Menu(screen='my_subscription' if has_sub else 'subscription').pack()))
    kb.row(types.InlineKeyboardButton(
        text='💰 Пополнить баланс', callback_data=Menu(screen='payments').pack()))

    if await settings.flag('features.referrals_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='🫂 Пригласить', callback_data=Menu(screen='referrals').pack()))
    if await settings.flag('features.gifts_enabled'):
        kb.add(types.InlineKeyboardButton(
            text='🎁 Подарить', callback_data=Menu(screen='gifts').pack()))
    if await settings.flag('features.promo_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='🎟 Промокод', callback_data=Menu(screen='promo').pack()))

    return await footer(kb, settings, back=None)


async def show_profile(event, c, user: dict, settings) -> None:
    await render(event, Screen(
        text=profile_caption(user),
        markup=(await profile_keyboard(user, settings)).as_markup(),
        image=c.media('profile'),
    ))


async def profile(call: types.CallbackQuery, c, user: dict, settings):
    await show_profile(call, c, user, settings)
    await call.answer()


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='profile')
    router.callback_query.register(profile, Menu.filter(F.screen == 'profile'))
    return router
