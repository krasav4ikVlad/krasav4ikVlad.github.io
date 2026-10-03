"""Активация промокода."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.content.emoji import e

MESSAGES = {
    'not_found': f'{e("cross")} Промокод не найден или больше не действует.',
    'used': f'{e("cross")} Вы уже активировали этот промокод.',
    'limit': f'{e("cross")} У промокода закончился лимит активаций.',
    'no_bypass': f'{e("cross")} Нет ByPass-подписки, на которую можно начислить гигабайты.',
    'panel_error': f'{e("cross")} Не удалось начислить награду. Попробуйте позже.',
    'bad_type': f'{e("cross")} Неизвестный тип награды.',
    'disabled': f'{e("cross")} Промокоды временно отключены.',
}


class PromoInput(StatesGroup):
    code = State()


async def ask_code(call: types.CallbackQuery, state: FSMContext, settings):
    await state.set_state(PromoInput.code)

    kb = await footer(InlineKeyboardBuilder(), settings, back='profile')
    await render(call, Screen(
        text=f'<b>{e("promo")} Промокод</b>\n\nОтправьте код одним сообщением.',
        markup=kb.as_markup()))
    await call.answer()


async def apply_code(message: types.Message, state: FSMContext, c, settings):
    result = await c.promo.redeem(message.from_user.id, message.text or '')

    if not result.ok:
        await message.answer(MESSAGES.get(result.reason, MESSAGES['not_found']))
        return

    await state.clear()
    kb = await footer(InlineKeyboardBuilder(), settings, back='profile')
    await message.answer(
        f'<b>{e("ok")} Промокод активирован</b>\n\n<b>Награда:</b> {result.reward}',
        reply_markup=kb.as_markup())


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='promo')
    router.callback_query.register(ask_code, Menu.filter(F.screen == 'promo'), Feature('features.promo_enabled'))
    router.message.register(apply_code, PromoInput.code)
    return router
