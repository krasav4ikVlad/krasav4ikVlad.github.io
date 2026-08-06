"""Подарочные подписки: витрина и inline-режим для отправки другу."""

from __future__ import annotations

import hashlib

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import gifts_block, profile_caption


async def gift_labels(plans_repo) -> dict[str, str]:
    """Коды тарифов → названия: в тексте должно быть «1 месяц», а не «1month»."""
    return {plan['code']: plan['title'] for plan in await plans_repo.all(only_enabled=False)}


async def gifts_menu(call: types.CallbackQuery, c, user: dict, settings):
    username = await settings.get('link.bot_username')
    labels = await gift_labels(c.plans)
    owned = c.users.pick(user, 'info.gifts', {}) or {}

    text = (
        profile_caption(user, '🎁 Подарки')
        + f'<b>🎁 Подарки:</b>\n{gifts_block(owned, labels)}\n\n'
        + f'<blockquote>🎁 Чтобы подарить RS VPN другу, просто напишите '
          f'@{username} прямо в чате с ним и выберите нужную подписку.\n\n'
          f'Или выберите тариф кнопкой ниже — бот подготовит ссылку.</blockquote>'
    )

    kb = InlineKeyboardBuilder()
    for plan in await c.plans.all():
        free = int(owned.get(plan['code'], 0) or 0)
        mark = f' (бесплатно: {free})' if free else f' — {plan["price"]}₽'
        kb.row(types.InlineKeyboardButton(
            text=f'🎁 {plan["title"]}{mark}',
            switch_inline_query=plan['code']))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('gifts')))
    await call.answer()


async def inline_gifts(query: types.InlineQuery, c, settings):
    if not await settings.flag('features.gifts_enabled'):
        await query.answer([], cache_time=1)
        return

    username = await settings.get('link.bot_username')
    wanted = (query.query or '').strip().lower()
    results = []

    for plan in await c.plans.all():
        # текст запроса приходит из switch_inline_query — показываем выбранный тариф
        if wanted and wanted not in (plan['code'].lower(), plan['title'].lower()):
            continue

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
    """Собирает роутер раздела."""
    router = Router(name='gifts')
    router.callback_query.register(gifts_menu, Menu.filter(F.screen == 'gifts'),
                                   Feature('features.gifts_enabled'))
    router.inline_query.register(inline_gifts)
    return router
