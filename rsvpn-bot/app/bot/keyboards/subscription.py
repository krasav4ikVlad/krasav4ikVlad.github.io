"""Клавиатура тарифов — строится из БД, а не из четырёх строк с ценами."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Plan


async def plans_keyboard(plans_repo, balance: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for plan in await plans_repo.all():
        gift = ' + 🎁' if plan.get('gift_count') else ''
        builder.row(types.InlineKeyboardButton(
            text=f'{plan["title"]}{gift} — {plan["price"]}₽',
            callback_data=Plan(action='buy', code=plan['code']).pack(),
        ))
    return builder
