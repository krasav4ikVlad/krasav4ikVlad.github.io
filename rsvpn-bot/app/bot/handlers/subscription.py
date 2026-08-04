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
from app.content import texts
from app.core.errors import NotEnoughBalance

router = Router(name='subscription')


@router.callback_query(Menu.filter(F.screen == 'subscription'))
async def show_plans(call: types.CallbackQuery, c, user: dict, settings):
    builder = await plans_keyboard(c.plans, c.users.pick(user, 'info.balance', 0))
    await footer(builder, settings, back='profile')

    await render(call, Screen(
        text=texts.render('screen.subscription.empty'),
        markup=builder.as_markup(),
        image=c.media('subscription'),
    ))
    await call.answer()


@router.callback_query(Plan.filter(F.action == 'buy'), Feature('features.buy_enabled'))
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


@router.callback_query(Menu.filter(F.screen == 'extend'), Feature('features.extend_enabled'))
async def extend(call: types.CallbackQuery, c, settings):
    await c.billing.extend(call.from_user.id)
    await call.answer('Подписка продлена ✅')
    await show_subscription(call, c, await c.users.get(call.from_user.id), settings)


@router.callback_query(Menu.filter(F.screen == 'my_subscription'))
async def show_subscription(call: types.CallbackQuery, c, user: dict, settings):
    """TODO: перенести экран из старого start.py (ветка `call.data.endswith(':sub')`).

    Текст собирается через texts.render('screen.subscription.*'), клавиатура —
    в keyboards/subscription.py. Ничего кроме сборки экрана здесь быть не должно.
    """
    raise NotImplementedError
