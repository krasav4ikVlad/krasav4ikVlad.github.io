"""Клавиатура тарифов — строится из БД, а не из четырёх строк с ценами."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Plan
from app.content.emoji import e


async def plans_keyboard(plans_repo, balance: int, action: str = 'buy',
                         current: str = '') -> InlineKeyboardBuilder:
    """Список тарифов.

    action='buy' — покупка (списывает деньги), action='change' — смена
    длительности у действующей подписки (только меняет период, деньги не
    трогает: спишутся при следующем продлении).
    """
    builder = InlineKeyboardBuilder()
    for plan in await plans_repo.all():
        gift = f' + {e("gift")}' if plan.get('gift_count') else ''
        mark = f'{e("ok")} ' if action == 'change' and plan['code'] == current else ''
        builder.row(types.InlineKeyboardButton(
            text=f'{mark}{plan["title"]}{gift} — {plan["price"]}₽',
            callback_data=Plan(action=action, code=plan['code']).pack(),
        ))
    return builder
