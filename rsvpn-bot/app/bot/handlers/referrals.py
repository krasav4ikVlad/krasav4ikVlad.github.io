"""Реферальная программа и заявки на вывод."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render

PAYOUT_MESSAGES = {
    'below_min': 'Минимальная сумма вывода — {minimum}₽.',
    'pending': 'Заявка уже в обработке. Дождитесь её завершения.',
    'cooldown': 'Заявку можно оформлять раз в {cooldown} ч. Осталось ждать {wait} ч.',
    'disabled': 'Вывод временно недоступен.',
}


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
            text='📤 Заказать вывод', callback_data=Menu(screen='payout').pack()))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    await call.answer()


async def request_payout(call: types.CallbackQuery, c, settings):
    result = await c.payouts.request(call.from_user.id)

    if not result.ok:
        template = PAYOUT_MESSAGES.get(result.reason, 'Заявку оформить не удалось.')
        await call.answer(template.format(
            minimum=await settings.int('payout.min_withdraw'),
            cooldown=await settings.int('payout.cooldown_hours'),
            wait=result.wait_hours), show_alert=True)
        return

    if c.notifier:
        sent = await c.notifier.payout_requested(call.from_user.id, amount=result.amount,
                                                 method=result.method)
        if not sent:
            # заявка не дошла до админов — снимаем метку, чтобы человек не завис
            await c.payouts.cancel_request(call.from_user.id)
            await call.answer('Не удалось отправить заявку. Попробуйте позже.', show_alert=True)
            return

    await call.answer(f'Заявка на {result.amount}₽ отправлена ✅', show_alert=True)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='referrals')
    router.callback_query.register(referrals, Menu.filter(F.screen == 'referrals'), Feature('features.referrals_enabled'))
    router.callback_query.register(request_payout, Menu.filter(F.screen == 'payout'), Feature('features.payouts_enabled'))
    return router
