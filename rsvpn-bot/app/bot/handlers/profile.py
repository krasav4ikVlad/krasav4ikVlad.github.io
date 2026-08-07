"""Экран профиля и привязка почты."""

from __future__ import annotations

import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.content.emoji import e

EMAIL_RE = re.compile(r'^[\w.+-]+@[\w-]+\.[\w.]+$')
NO_EMAIL = 'Не привязана'


class EmailInput(StatesGroup):
    value = State()


async def profile_keyboard(user: dict, settings, trial=None) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    has_sub = bool((user.get('vpn') or {}).get('shortUuid'))

    # Бесплатный период — первым: это главное, что может сделать новичок,
    # и пока он не забран, кнопка покупки для человека вторична
    if trial is not None and await trial.available(user):
        days = await settings.int('price.trial_days')
        kb.row(types.InlineKeyboardButton(
            text=f'{e("gift")} {days} дня бесплатно', callback_data=Menu(screen='trial').pack()))

    kb.row(types.InlineKeyboardButton(
        text=f'{e("shield")} Ваша подписка' if has_sub else f'{e("plus")} Подключить RS VPN',
        callback_data=Menu(screen='my_subscription' if has_sub else 'subscription').pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("money")} Пополнить баланс', callback_data=Menu(screen='payments').pack()))

    if await settings.flag('features.referrals_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("referrals")} Пригласить', callback_data=Menu(screen='referrals').pack()))
    if await settings.flag('features.gifts_enabled'):
        kb.add(types.InlineKeyboardButton(
            text=f'{e("gift")} Подарить', callback_data=Menu(screen='gifts').pack()))

    # почта нужна для чеков и восстановления доступа — кнопка должна быть на виду
    email = (user.get('info') or {}).get('email') or NO_EMAIL
    kb.row(types.InlineKeyboardButton(
        text=f'{e("mail")} Изменить почту' if email != NO_EMAIL else f'{e("mail")} Привязать почту',
        callback_data=Menu(screen='email').pack()))

    if await settings.flag('features.promo_enabled'):
        kb.add(types.InlineKeyboardButton(
            text=f'{e("promo")} Промокод', callback_data=Menu(screen='promo').pack()))

    return await footer(kb, settings, back=None)


async def show_profile(event, c, user: dict, settings) -> None:
    await render(event, Screen(
        text=profile_caption(user),
        markup=(await profile_keyboard(user, settings, c.trial)).as_markup(),
        image=c.media('profile'),
    ))


async def profile(call: types.CallbackQuery, state: FSMContext, c, user: dict, settings):
    await state.clear()
    await show_profile(call, c, user, settings)
    await call.answer()


async def ask_email(call: types.CallbackQuery, state: FSMContext, c, user: dict, settings):
    await state.set_state(EmailInput.value)

    current = (user.get('info') or {}).get('email') or NO_EMAIL
    kb = await footer(InlineKeyboardBuilder(), settings, back='profile')

    await render(call, Screen(
        text=(f'<b>{e("mail")} Почта</b>\n\n'
              f'<b>Сейчас:</b> <code>{current}</code>\n\n'
              'Отправьте адрес одним сообщением.\n'
              '<blockquote>Она нужна для чеков об оплате и восстановления '
              'доступа к подписке.</blockquote>'),
        markup=kb.as_markup(), image=c.media('email')))
    await call.answer()


async def save_email(message: types.Message, state: FSMContext, c, settings):
    email = (message.text or '').strip()

    if not EMAIL_RE.match(email):
        await message.answer(f'{e("warning")}Это не похоже на адрес почты. Пример: name@example.com')
        return

    await c.users.col.update_one(
        {'user_data.user_id': message.from_user.id},
        {'$set': {'info.email': email}})
    await state.clear()

    if c.notifier:
        await c.notifier.email_changed(message.from_user.id, email=email)

    user = await c.users.get(message.from_user.id)
    await message.answer(f'{e("ok")} Почта сохранена: <code>{email}</code>')
    await show_profile(message, c, user, settings)


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='profile')
    router.callback_query.register(profile, Menu.filter(F.screen == 'profile'))
    router.callback_query.register(ask_email, Menu.filter(F.screen == 'email'))
    router.message.register(save_email, EmailInput.value)
    return router
