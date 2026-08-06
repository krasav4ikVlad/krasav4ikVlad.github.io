"""Реферальная программа и заявки на вывод."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render


async def referrals(call: types.CallbackQuery, c, user: dict, settings):
    stats = c.users.pick(user, 'info.ref_stats', {}) or {}
    percent = round(await settings.rate('bonus.ref_rate') * 100)
    username = await settings.get('link.bot_username')

    text = (
        '<b>🫂 Реферальная программа</b>\n\n'
        f'<b>Ваша ссылка:</b>\n<code>https://t.me/{username}?start=ref_{call.from_user.id}</code>\n\n'
        f'<b>Друзей:</b> <code>{len(stats.get("referrals") or [])}</code>\n'
        f'<b>Из них платящих:</b> <code>{len(stats.get("paying_referrals") or [])}</code>\n'
        f'<b>Заработано всего:</b> <code>{stats.get("earned_total", 0)}₽</code>\n'
        f'<b>Доступно к выводу:</b> <code>{stats.get("withdrawable", 0)}₽</code>\n\n'
        f'<blockquote>Вы получаете {percent}% с каждого пополнения приглашённого друга.</blockquote>'
    )

    kb = InlineKeyboardBuilder()
    if await settings.flag('features.payouts_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='📤 Вывести средства', callback_data=Menu(screen='payout').pack()))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    await call.answer()


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='referrals')
    router.callback_query.register(referrals, Menu.filter(F.screen == 'referrals'), Feature('features.referrals_enabled'))
    return router
