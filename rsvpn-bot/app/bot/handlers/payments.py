"""Пополнение баланса: выбор способа и суммы.

Список способов строится из реестра провайдеров — выключенный тумблером
провайдер просто не появляется на экране, отдельного `if` под каждый способ
больше нет.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu, Payment
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.core.errors import PaymentError

PRESETS = (75, 150, 300, 500, 1000)


class TopUp(StatesGroup):
    amount = State()


async def choose_provider(call: types.CallbackQuery, c, settings, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()

    for provider in await c.payments.available():
        kb.row(types.InlineKeyboardButton(
            text=provider.title, callback_data=Payment(provider=provider.code).pack()))

    await footer(kb, settings, back='profile')
    await render(call, Screen(
        text='<b>💰 Пополнение баланса</b>\n\nВыберите способ оплаты:',
        markup=kb.as_markup(), image=c.media('payment')))
    await call.answer()


async def choose_amount(call: types.CallbackQuery, callback_data: Payment, c, settings,
                        state: FSMContext):
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
    await footer(kb, settings, back='payments')

    await state.set_state(TopUp.amount)
    await state.update_data(provider=provider.code, minimum=minimum)

    await render(call, Screen(
        text=(f'<b>{provider.title}</b>\n\nВыберите сумму или отправьте свою сообщением.\n'
              f'Минимум: <b>{minimum}₽</b>'),
        markup=kb.as_markup()))
    await call.answer()


async def create_invoice(call: types.CallbackQuery, callback_data: Payment, c, state: FSMContext):
    await state.clear()
    await _send_invoice(call, c, callback_data.provider, callback_data.amount)


async def custom_amount(message: types.Message, state: FSMContext, c):
    data = await state.get_data()
    raw = (message.text or '').strip().replace(' ', '')

    if not raw.isdigit():
        await message.answer('❗️Отправьте сумму числом.')
        return

    amount = int(raw)
    if amount < data.get('minimum', 0):
        await message.answer(f'❗️Минимальная сумма — {data["minimum"]}₽.')
        return

    await state.clear()
    await _send_invoice(message, c, data['provider'], amount)


async def _send_invoice(event, c, provider_code: str, amount: int) -> None:
    provider = c.payments.get(provider_code)
    if not provider:
        await render(event, Screen(text='Способ оплаты недоступен.'))
        return

    try:
        invoice = await provider.create_invoice(
            event.from_user.id if hasattr(event, 'from_user') else 0, amount)
    except (PaymentError, NotImplementedError):
        await render(event, Screen(
            text='Платёжная система сейчас недоступна. Попробуйте другой способ.'))
        return

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text=f'Оплатить {amount}₽', url=invoice.url))
    kb.row(types.InlineKeyboardButton(text='⬅️ Назад', callback_data=Menu(screen='payments').pack()))

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
    router.callback_query.register(create_invoice, Payment.filter(F.amount > 0))
    router.message.register(custom_amount, TopUp.amount)
    return router
