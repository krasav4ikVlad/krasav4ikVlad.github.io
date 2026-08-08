"""Клавиатура тарифов — строится из БД, а не из четырёх строк с ценами."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Plan
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
        price = discounted(full, discount)
        money = f'{price}₽ вместо {full}₽' if price != full else f'{full}₽'
        builder.row(types.InlineKeyboardButton(
            text=f'{mark}{plan["title"]}{gift} — {money}',
            callback_data=Plan(action=action, code=plan['code']).pack(),
        ))
    return builder
