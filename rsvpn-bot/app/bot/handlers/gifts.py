"""Подарочные подписки: витрина и inline-режим для отправки другу."""

from __future__ import annotations

import hashlib

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render


async def gifts_menu(call: types.CallbackQuery, c, user: dict, settings):
    free = (c.users.pick(user, 'info.gifts', {}) or {})
    username = await settings.get('link.bot_username')

    lines = ['<b>🎁 Подарок другу</b>\n',
             'Выберите тариф — бот подготовит ссылку, которую можно переслать.\n']
    owned = [f'• {code}: {count} шт.' for code, count in free.items() if count]
    if owned:
        lines.append('<b>Бесплатные подарки:</b>')
        lines.extend(owned)
        lines.append('')
    lines.append(f'Можно также набрать <code>@{username}</code> в любом чате.')

    kb = InlineKeyboardBuilder()
    for plan in await c.plans.all():
        kb.row(types.InlineKeyboardButton(
            text=f'{plan["title"]} — {plan["price"]}₽',
            switch_inline_query=plan['code']))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text='\n'.join(lines), markup=kb.as_markup(),
                              image=c.media('gifts')))
    await call.answer()


async def inline_gifts(query: types.InlineQuery, c, settings):
    if not await settings.flag('features.gifts_enabled'):
        await query.answer([], cache_time=1)
        return

    username = await settings.get('link.bot_username')
    results = []

    for plan in await c.plans.all():
        gift_id = await c.gifts.create(query.from_user.id, plan['code'])
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(
            text='🎁 Принять подарок',
            url=f'https://t.me/{username}?start=gift_{gift_id}_{plan["code"]}_{query.from_user.id}'))

        results.append(types.InlineQueryResultArticle(
            id=hashlib.md5(f'{plan["code"]}{gift_id}'.encode()).hexdigest(),
            title=f'🎁 Подарить {plan["title"]}',
            description=f'С баланса спишется {plan["price"]}₽ после принятия',
            input_message_content=types.InputTextMessageContent(
                message_text=(f'<b>🎁 {query.from_user.full_name} дарит вам '
                              f'RS VPN на {plan["days"]} дней!</b>\n\n'
                              f'Нажмите кнопку ниже, чтобы активировать.')),
            reply_markup=kb.as_markup(),
        ))

    await query.answer(results, cache_time=1, is_personal=True)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='gifts')
    router.callback_query.register(gifts_menu, Menu.filter(F.screen == 'gifts'), Feature('features.gifts_enabled'))
    router.inline_query.register(inline_gifts)
    return router
