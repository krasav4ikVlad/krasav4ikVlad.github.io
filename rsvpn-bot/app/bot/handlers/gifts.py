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
from app.content.emoji import e


async def gift_labels(plans_repo) -> dict[str, str]:
    """Коды тарифов → названия: в тексте должно быть «1 месяц», а не «1month»."""
    return {plan['code']: plan['title'] for plan in await plans_repo.all(only_enabled=False)}


async def gifts_menu(call: types.CallbackQuery, c, user: dict, settings):
    username = await settings.get('link.bot_username')
    labels = await gift_labels(c.plans)
    owned = c.users.pick(user, 'info.gifts', {}) or {}

    text = (
        profile_caption(user, f'{e("gift")} Подарки')
        + f'<b>{e("gift")} Подарки:</b>\n{gifts_block(owned, labels)}\n\n'
        + f'<blockquote>{e("gift")} Чтобы подарить RS VPN другу, просто напишите '
          f'@{username} прямо в чате с ним и выберите нужную подписку.\n\n'
          f'Или выберите тариф кнопкой ниже — бот подготовит ссылку.</blockquote>'
    )

    kb = InlineKeyboardBuilder()
    for plan in await c.plans.all():
        free = int(owned.get(plan['code'], 0) or 0)
        mark = f' (бесплатно: {free})' if free else f' — {plan["price"]}₽'
        kb.row(types.InlineKeyboardButton(
            text=f'{e("gift")} {plan["title"]}{mark}',
            switch_inline_query=plan['code']))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('gifts')))
    await call.answer()


async def inline_gifts(query: types.InlineQuery, c, settings):
    """Витрина подарков при упоминании бота в чужом чате.

    Пустой ответ Telegram показывает как «ничего не найдено» — то есть
    выключённые подарки и отсутствие тарифов выглядят ровно так же, как
    сломанный бот. Поэтому вместо пустоты отдаём кнопку с объяснением.
    """
    if not await settings.flag('features.gifts_enabled'):
        await _nothing(query, 'Подарки временно отключены')
        return

    username = await settings.get('link.bot_username')
    wanted = (query.query or '').strip().lower()
    results = []

    for plan in await c.plans.all():
        # текст запроса приходит из switch_inline_query — показываем выбранный тариф
        if wanted and wanted not in (plan['code'].lower(), plan['title'].lower()):
            continue

        gift_id = await c.gifts.pending(query.from_user.id, plan['code'])
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(
            text=f'{e("gift")} Принять подарок',
            url=f'https://t.me/{username}?start=gift_{gift_id}_{plan["code"]}_{query.from_user.id}'))

        results.append(types.InlineQueryResultArticle(
            id=hashlib.md5(f'{plan["code"]}{gift_id}'.encode()).hexdigest(),
            title=f'{e("gift")} Подарить {plan["title"]}',
            description=f'С баланса спишется {plan["price"]}₽ после принятия',
            input_message_content=types.InputTextMessageContent(
                message_text=(f'<b>{e("gift")} {query.from_user.full_name} дарит вам '
                              f'RS VPN на {plan["days"]} дней!</b>\n\n'
                              f'Нажмите кнопку ниже, чтобы активировать.')),
            reply_markup=kb.as_markup(),
        ))

    if not results:
        await _nothing(query, 'Подходящих тарифов нет')
        return

    await query.answer(results, cache_time=1, is_personal=True)


async def _nothing(query: types.InlineQuery, reason: str) -> None:
    """Ответ «показывать нечего» с кнопкой в бота вместо молчания."""
    await query.answer(
        [], cache_time=1, is_personal=True,
        button=types.InlineQueryResultsButton(text=reason, start_parameter='gifts'))


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='gifts')
    router.callback_query.register(gifts_menu, Menu.filter(F.screen == 'gifts'),
                                   Feature('features.gifts_enabled'))
    router.inline_query.register(inline_gifts)
    return router
