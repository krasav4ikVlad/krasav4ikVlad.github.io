"""Пополнение баланса: выбор способа и суммы.

Список способов строится из реестра провайдеров — выключенный тумблером
провайдер просто не появляется на экране, отдельного `if` под каждый способ
больше нет.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu, Payment
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.pricing import price_line_for
from app.bot.screens.profile import profile_caption
from app.core.errors import PaymentError
from app.content.emoji import e

log = logging.getLogger(__name__)

PRESETS = (75, 150, 300, 500, 1000)


class TopUp(StatesGroup):
    amount = State()


async def bonus_line(c, user: dict, settings) -> str:
    """«При пополнении сегодня +30% сверху».

    Бонус начисляется в TopupService и без этой строки остаётся невидимым:
    деньги мы отдаём, а на решение пополнить это никак не влияет.
    Условие здесь то же самое, что и при начислении, — иначе экран пообещает
    то, чего человек не получит.
    """
    from app.services.topup import AB_BONUS_GROUPS

    growth = user.get('growth') or {}
    if growth.get('ab_group') not in AB_BONUS_GROUPS:
        return ''
    if not str(growth.get('segment') or '').startswith('new_trial'):
        return ''

    percent = round(await settings.rate('bonus.ab_new_trial_rate') * 100)
    if percent <= 0:
        return ''
    return (f'\n<blockquote>{e("gift")} При пополнении сегодня вы получите '
            f'<b>+{percent}% сверху</b> — предложение для новых пользователей.</blockquote>')


async def topup_caption(c, user: dict, settings, title: str = f'{e("money")} Пополнение баланса',
                        tail: str = '') -> str:
    """Шапка экранов пополнения: кто, сколько на балансе, сколько стоит подписка."""
    price = (await price_line_for(c, user) if c.users.pick(user, 'vpn.shortUuid')
             else '<code>Подписка не оформлена</code>')
    return (profile_caption(user, title)
            + f'<b>{e("payout")} Плата за подписку:</b> {price}\n\n'
            + tail + await bonus_line(c, user, settings))


async def choose_provider(call: types.CallbackQuery, c, user: dict, settings,
                          state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()

    for provider in await c.payments.available():
        # у Tribute оплата целиком в его мини-аппе: он сам спрашивает сумму,
        # поэтому кнопка ведёт наружу, а не в наш экран выбора суммы
        kb.row(types.InlineKeyboardButton(text=provider.title, url=provider.direct_url)
               if provider.direct_url else
               types.InlineKeyboardButton(
                   text=provider.title,
                   callback_data=Payment(provider=provider.code).pack()))

    await footer(kb, settings, back='profile')
    await render(call, Screen(
        text=await topup_caption(c, user, settings,
                                 tail=f'<blockquote>{e("money")} Выберите способ оплаты.</blockquote>'),
        markup=kb.as_markup(), image=c.media('payment')))
    await call.answer()


async def choose_amount(call: types.CallbackQuery, callback_data: Payment, c, user: dict,
                        settings, state: FSMContext):
    provider = c.payments.get(callback_data.provider)
    if not provider:
        await call.answer('Способ недоступен', show_alert=True)
        return

    minimum = max(provider.min_amount, await settings.int('pay.min_topup'))
    kb = InlineKeyboardBuilder()
    for amount in PRESETS:
        if amount >= minimum:
            kb.add(types.InlineKeyboardButton(
                text=f'{amount}₽',
                callback_data=Payment(provider=provider.code, amount=amount).pack()))
    kb.adjust(3)
    kb.row(types.InlineKeyboardButton(
        text=f'{e("edit")} Своя сумма',
        callback_data=Payment(provider=provider.code, amount=-1).pack()))
    await footer(kb, settings, back='payments')

    await state.set_state(TopUp.amount)
    await state.update_data(provider=provider.code, minimum=minimum)

    await render(call, Screen(
        text=await topup_caption(
            c, user, settings, title=f'{e("money")} {provider.title}',
            tail=(f'<blockquote>{e("money")} Выберите сумму кнопкой или отправьте свою сообщением.\n'
                  f'Минимальная сумма пополнения — {minimum}₽.</blockquote>')),
        markup=kb.as_markup(), image=c.media('payment')))
    await call.answer()


async def ask_custom_amount(call: types.CallbackQuery, callback_data: Payment, c,
                            settings, state: FSMContext):
    data = await state.get_data()
    minimum = data.get('minimum') or await settings.int('pay.min_topup')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Payment(provider=callback_data.provider).pack()))

    await state.set_state(TopUp.amount)
    await state.update_data(provider=callback_data.provider, minimum=minimum)

    await render(call, Screen(
        text=f'Отправьте сумму пополнения числом.\nМинимум: <b>{minimum}₽</b>',
        markup=kb.as_markup()))
    await call.answer()


async def create_invoice(call: types.CallbackQuery, callback_data: Payment, c, state: FSMContext):
    await state.clear()
    await _send_invoice(call, c, callback_data.provider, callback_data.amount)


async def custom_amount(message: types.Message, state: FSMContext, c):
    data = await state.get_data()
    raw = (message.text or '').strip().replace(' ', '')

    if not raw.isdigit():
        await message.answer(f'{e("warning")}Отправьте сумму числом.')
        return

    amount = int(raw)
    if amount < data.get('minimum', 0):
        await message.answer(f'{e("warning")}Минимальная сумма — {data["minimum"]}₽.')
        return

    await state.clear()
    await _send_invoice(message, c, data['provider'], amount)


async def _send_invoice(event, c, provider_code: str, amount: int) -> None:
    provider = c.payments.get(provider_code)
    if not provider:
        await render(event, Screen(text='Способ оплаты недоступен.'))
        return

    try:
        invoice = await provider.create_invoice(event.from_user.id, amount)
    except NotImplementedError:
        log.error('у провайдера %s не реализовано создание счёта', provider_code)
        await render(event, Screen(
            text='Этот способ оплаты пока недоступен. Выберите другой.'))
        return
    except PaymentError as exc:
        log.error('счёт не создан (%s): %s', provider_code, exc)
        await render(event, Screen(
            text=('Платёжная система не ответила. Попробуйте ещё раз или '
                  'выберите другой способ.')))
        return

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text=f'Оплатить {amount}₽', url=invoice.url))
    kb.row(types.InlineKeyboardButton(text=f'{e("back")} Назад', callback_data=Menu(screen='payments').pack()))

    await render(event, Screen(
        text=(f'<b>Счёт на {amount}₽</b>\n\nПосле оплаты баланс пополнится автоматически — '
              f'обычно это занимает меньше минуты.'),
        markup=kb.as_markup()))


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='payments')
    router.callback_query.register(choose_provider, Menu.filter(F.screen == 'payments'))
    router.callback_query.register(choose_amount, Payment.filter(F.amount == 0))
    router.callback_query.register(ask_custom_amount, Payment.filter(F.amount == -1))
    router.callback_query.register(create_invoice, Payment.filter(F.amount > 0))
    router.message.register(custom_amount, TopUp.amount)
    return router
