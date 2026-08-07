"""Бесплатный период за подписку на канал.

Заменил стартовый баланс. Разница по сути: деньги начислялись всем подряд
за нажатие /start, включая случайных и вернувшихся с нового аккаунта, — а
бесплатные дни выдаются один раз и за понятное действие.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption

REASONS = {
    'not_subscribed': 'Подписка на канал не найдена. Подпишитесь и нажмите «Проверить».',
    'claimed': 'Бесплатный период уже был активирован.',
    'has_sub': 'У вас уже есть подписка.',
    'disabled': 'Бесплатный период сейчас недоступен.',
    'panel': 'Не удалось выдать подписку, попробуйте через минуту.',
    'check': 'Не удалось проверить подписку. Напишите в поддержку — мы разберёмся.',
}


async def trial_screen(event, c, user: dict, settings, note: str = ''):
    days = await settings.int('price.trial_days')
    channel_url = await settings.get('link.channel')

    kb = InlineKeyboardBuilder()
    if await settings.flag('trial.require_subscription'):
        kb.row(types.InlineKeyboardButton(text='📢 Подписаться на канал', url=channel_url))
    kb.row(types.InlineKeyboardButton(
        text='✅ Проверить и получить', callback_data=Menu(screen='trial_claim').pack()))
    kb.row(types.InlineKeyboardButton(
        text='💳 Купить подписку', callback_data=Menu(screen='subscription').pack()))
    await footer(kb, settings, back='profile')

    hint = note or (
        f'🎁 Подпишитесь на наш канал и получите <b>{days} дня</b> RS VPN бесплатно.\n\n'
        'После подписки нажмите «Проверить и получить» — доступ включится сразу.'
        if await settings.flag('trial.require_subscription') else
        f'🎁 Заберите <b>{days} дня</b> RS VPN бесплатно — доступ включится сразу.')

    await render(event, Screen(
        text=profile_caption(user, '🎁 Бесплатный период') + f'<blockquote>{hint}</blockquote>',
        markup=kb.as_markup(), image=c.media('duration')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def claim(call: types.CallbackQuery, c, user: dict, settings):
    result = await c.trial.claim(call.from_user.id)

    if not result.ok:
        await call.answer(REASONS.get(result.reason, REASONS['disabled']), show_alert=True)
        # «не подписан» — единственная причина, которую человек может
        # исправить сам, поэтому оставляем его на экране с кнопками
        if result.reason == 'not_subscribed':
            return
        from app.bot.handlers.profile import show_profile
        await show_profile(call, c, await c.users.get(call.from_user.id), settings)
        return

    await call.answer(f'Готово! {result.days} дня RS VPN активированы ✅', show_alert=True)
    from app.bot.handlers.subscription import show_subscription
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='trial')
    feature = Feature('features.trial_enabled')

    router.callback_query.register(trial_screen, Menu.filter(F.screen == 'trial'), feature)
    router.callback_query.register(claim, Menu.filter(F.screen == 'trial_claim'), feature)
    return router
