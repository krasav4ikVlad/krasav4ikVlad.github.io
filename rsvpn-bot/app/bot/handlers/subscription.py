"""Подписка: показать тарифы, купить, продлить.

Хендлер отвечает только за «показать / спросить / ответить». Вся логика —
в BillingService, поэтому здесь нет ни обращений к Mongo, ни расчёта цен,
ни вызовов панели. Это и есть критерий «хендлер написан правильно»:
если в нём появился users.update_one — логика утекла не туда.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu, Plan
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.keyboards.subscription import plans_keyboard
from app.bot.screens.base import Screen, render
from app.bot.screens.pricing import price_line_for
from app.bot.screens.profile import profile_caption, subscription_block
from app.content import texts
from app.core.errors import NotEnoughBalance
from app.core.time import now, parse_dt


async def show_plans(event, c, user: dict, settings):
    builder = await plans_keyboard(c.plans, c.users.pick(user, 'info.balance', 0))
    await footer(builder, settings, back='profile')

    await render(event, Screen(
        text=profile_caption(user) + texts.render('screen.subscription.empty'),
        markup=builder.as_markup(),
        image=c.media('subscription'),
    ))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def buy_plan(call: types.CallbackQuery, callback_data: Plan, c, settings):
    try:
        result = await c.billing.buy(call.from_user.id, callback_data.code)
    except NotEnoughBalance as exc:
        builder = await footer(InlineKeyboardBuilder(), settings, back='payments')
        await render(call, Screen(
            text=texts.render('screen.balance.not_enough', missing=exc.need - exc.have),
            markup=builder.as_markup(),
            image=c.media('no_funds'),
        ))
        await call.answer()
        return

    await call.answer(f'Подписка «{result["plan"]["title"]}» активирована ✅')
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


async def extend(call: types.CallbackQuery, c, settings):
    await c.billing.extend(call.from_user.id)
    await call.answer('Подписка продлена ✅')
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


async def show_subscription(event, c, user: dict, settings):
    """Экран действующей подписки."""
    vpn = user.get('vpn') or {}
    if not vpn.get('shortUuid'):
        return await show_plans(event, c, user, settings)

    price = await price_line_for(c, user)
    connect_base = await settings.get('link.connect_base')
    expire = parse_dt(vpn.get('expireAt'))

    text = profile_caption(user) + subscription_block(user, price, connect_base)
    if not expire or expire <= now():
        text += f'<blockquote>{texts.render("screen.subscription.expired")}</blockquote>'

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text='🛡 Настроить VPN', url=f'{connect_base}{vpn["shortUuid"]}'))
    if await settings.flag('features.extend_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='🔁 Продлить подписку', callback_data=Menu(screen='extend').pack()))
    if await settings.flag('features.bypass_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='🛡 ByPass подписка', callback_data=Menu(screen='bypass').pack()))
    if await settings.flag('features.devices_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='📲 Менеджер устройств', callback_data=Menu(screen='devices').pack()))
    if await settings.flag('features.change_period_enabled'):
        kb.row(types.InlineKeyboardButton(
            text='📅 Изменить длительность', callback_data=Menu(screen='subscription').pack()))
    await footer(kb, settings, back='profile')

    await render(event, Screen(text=text, markup=kb.as_markup(),
                               image=c.media('subscription_active')))


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='subscription')
    router.callback_query.register(show_plans, Menu.filter(F.screen == 'subscription'))
    router.callback_query.register(buy_plan, Plan.filter(F.action == 'buy'), Feature('features.buy_enabled'))
    router.callback_query.register(extend, Menu.filter(F.screen == 'extend'), Feature('features.extend_enabled'))
    router.callback_query.register(show_subscription, Menu.filter(F.screen == 'my_subscription'))
    return router
