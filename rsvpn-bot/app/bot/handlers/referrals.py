"""Реферальная программа и заявки на вывод."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.domain.referrals import referral_stats


async def referrals(call: types.CallbackQuery, c, user: dict, settings):
    stats = referral_stats(c.users.pick(user, 'info.ref_stats', {}))
    percent = round(await settings.rate('bonus.ref_rate') * 100)
    username = await settings.get('link.bot_username')

    text = (
        profile_caption(user, '🫂 Реферальная программа')
        + f'<b>🔗 Ваша ссылка:</b>\n'
          f'<code>https://t.me/{username}?start=ref_{call.from_user.id}</code>\n\n'
        + '<b>📊 Статистика:</b>\n'
          f'— 🫂 Приглашено друзей: <code>{stats.invited}</code>\n'
          f'— 🗣 Активных: <code>{stats.active}</code> '
          f'(<code>{stats.active_percent}%</code>)\n'
          f'— 💰 Оплат от друзей: <code>{stats.payments}</code>\n'
          f'— 💳 Средний чек: <code>{stats.average_payment} ₽</code>\n'
          f'— 📈 Доход с 1 активного друга: <code>~{stats.per_active_friend} ₽</code>\n'
          f'— 💸 Всего заработано: <code>{stats.earned} ₽</code>\n'
          f'— 💱 Доступно к выводу: <code>{stats.withdrawable} ₽</code>\n\n'
        + f'<blockquote>🫂 Вы получаете {percent}% с каждого пополнения '
          f'приглашённого друга — без ограничения по времени и количеству.</blockquote>'
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
