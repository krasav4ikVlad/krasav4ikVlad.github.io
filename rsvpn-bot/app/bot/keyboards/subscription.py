"""Клавиатура тарифов — строится из БД, а не из четырёх строк с ценами."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Plan
from app.bot.screens.pricing import price_tag
from app.content.emoji import e
from app.domain.pricing import discounted


async def plans_keyboard(plans_repo, balance: int, action: str = 'buy',
                         current: str = '', discount: float = 0.0) -> InlineKeyboardBuilder:
    """Список тарифов.

    action='buy' — покупка (списывает деньги), action='change' — смена
    длительности у действующей подписки (только меняет период, деньги не
    трогает: спишутся при следующем продлении).

    discount — скидка аудитории. В кнопке стоит уже уценённая цена: именно
    она и спишется, а полная показана рядом зачёркнутой по смыслу («вместо»),
    иначе акция не видна и выглядит как ошибка в прайсе.
    """
    builder = InlineKeyboardBuilder()
    for plan in await plans_repo.all():
        gift = f' + {e("gift")}' if plan.get('gift_count') else ''
        mark = f'{e("ok")} ' if action == 'change' and plan['code'] == current else ''
        full = int(plan['price'])
        builder.row(types.InlineKeyboardButton(
            text=f'{mark}{plan["title"]}{gift} — '
                 f'{price_tag(full, discounted(full, discount), html=False)}',
            callback_data=Plan(action=action, code=plan['code']).pack(),
        ))
    return builder
