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
from app.bot.screens.profile import period_label, profile_caption, subscription_block
from app.content import texts
from app.core.errors import NotEnoughBalance
from app.core.time import now, parse_dt
from app.content.emoji import e


async def show_plans(event, c, user: dict, settings):
    discount = await c.discounts.rate(user)
    builder = await plans_keyboard(c.plans, c.users.pick(user, 'info.balance', 0),
                                   discount=discount)
    await footer(builder, settings, back='profile')

    notice = await c.discounts.notice(user)
    await render(event, Screen(
        text=(profile_caption(user) + texts.render('screen.subscription.empty')
              + (f'\n\n<b>{e("discount")} {notice}</b>' if notice else '')),
        markup=builder.as_markup(),
        image=c.media('subscription'),
    ))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def change_period(event, c, user: dict, settings, note: str = ''):
    """Выбор длительности для действующей подписки.

    Отдельный экран, а не тот же список тарифов: здесь ничего не покупается,
    поэтому ни баланс не проверяется, ни деньги не списываются.
    """
    vpn = user.get('vpn') or {}
    current = await c.plans.by_days(vpn.get('period') or 0)
    kb = await plans_keyboard(c.plans, 0, action='change',
                              current=(current or {}).get('code', ''),
                              discount=await c.discounts.rate(user))
    await footer(kb, settings, back='my_subscription')

    text = (profile_caption(user, f'{e("calendar")} Длительность подписки')
            + f'<b>{e("calendar")} Сейчас продлевается на:</b> '
              f'<code>{period_label(vpn.get("period") or 0)}</code>\n\n'
            + f'<blockquote>{note or texts.render("screen.subscription.change_period")}</blockquote>')

    await render(event, Screen(text=text, markup=kb.as_markup(),
                               image=c.media('duration')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def set_period(call: types.CallbackQuery, callback_data: Plan, c, user: dict, settings):
    """Меняет только период. Списание произойдёт при следующем продлении."""
    plan = await c.plans.get(callback_data.code)
    if not plan or not plan.get('enabled', True):
        await call.answer('Этот тариф сейчас недоступен', show_alert=True)
        return

    await c.users.set_vpn(call.from_user.id, {'period': int(plan['days'])})
    await call.answer(f'Длительность: {plan["title"]} {e("ok")}')
    fresh = await c.users.get(call.from_user.id)
    await change_period(call, c, fresh, settings,
                        note=f'Готово. При следующем продлении подписка продлится '
                             f'на {period_label(int(plan["days"]))} за '
                             f'{await c.discounts.price(fresh, plan)}₽. '
                             f'Текущая дата окончания не меняется.')


async def buy_plan(call: types.CallbackQuery, callback_data: Plan, c, settings):
    try:
        result = await c.billing.buy(call.from_user.id, callback_data.code)
    except NotEnoughBalance as exc:
        await not_enough(call, c, settings, exc)
        return

    await call.answer(f'Подписка «{result["plan"]["title"]}» активирована {e("ok")}')
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


async def extend(call: types.CallbackQuery, c, settings):
    try:
        await c.billing.extend(call.from_user.id)
    except NotEnoughBalance as exc:
        # тот же экран, что и при покупке: с кнопкой пополнения, а не
        # всплывающим текстом, из которого некуда идти
        await not_enough(call, c, settings, exc)
        return

    await call.answer(f'Подписка продлена {e("ok")}')
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


async def not_enough(call: types.CallbackQuery, c, settings, exc: NotEnoughBalance):
    builder = await footer(InlineKeyboardBuilder(), settings, back='payments')
    await render(call, Screen(
        text=texts.render('screen.balance.not_enough', missing=exc.need - exc.have),
        markup=builder.as_markup(),
        image=c.media('no_funds'),
    ))
    await call.answer()


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
        text=f'{e("shield")} Настроить VPN', url=f'{connect_base}{vpn["shortUuid"]}'))
    if await settings.flag('features.extend_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("renew")} Продлить подписку', callback_data=Menu(screen='extend').pack()))
    if await settings.flag('features.bypass_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("shield")} ByPass подписка', callback_data=Menu(screen='bypass').pack()))
    if await settings.flag('features.devices_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("devices")} Менеджер устройств', callback_data=Menu(screen='devices').pack()))
    if await settings.flag('features.change_period_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("calendar")} Изменить длительность', callback_data=Menu(screen='period').pack()))
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
    router.callback_query.register(change_period, Menu.filter(F.screen == 'period'), Feature('features.change_period_enabled'))
    router.callback_query.register(set_period, Plan.filter(F.action == 'change'), Feature('features.change_period_enabled'))
    router.callback_query.register(buy_plan, Plan.filter(F.action == 'buy'), Feature('features.buy_enabled'))
    router.callback_query.register(extend, Menu.filter(F.screen == 'extend'), Feature('features.extend_enabled'))
    router.callback_query.register(show_subscription, Menu.filter(F.screen == 'my_subscription'))
    return router
