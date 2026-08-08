"""Строка «Плата за подписку» и ценник со скидкой — одни на все экраны.

Формат взят из старого `make_price_line`: пользователю важно видеть цену
тарифа за его период и отдельно ежемесячную плату за устройства, а не одно
итоговое число (по нему непонятно, за что списывают).

Живёт в screens/, а не в domain/, потому что читает тариф и настройки;
сама арифметика по-прежнему в domain/pricing.py.

Про зачёркивание
────────────────
В тексте сообщения это тег <s>. В подписи кнопки разметки нет вообще —
там зачёркивание собирается символами: после каждого знака ставится
U+0336, «объединяющая черта». Выглядит одинаково, а больше в кнопке
ничего и не сделать.
"""

from __future__ import annotations

from app.bot.screens.profile import price_line
from app.domain.pricing import devices_price

STRIKE = '̶'      # объединяющая черта: рисуется поверх предыдущего знака


def strike(text: str) -> str:
    """Зачёркнутый текст без разметки — для подписей кнопок."""
    return ''.join(char + STRIKE for char in text)


def discount_percent(full: int, price: int) -> int:
    if full <= 0 or price >= full:
        return 0
    return round((1 - price / full) * 100)


def price_tag(full: int, price: int, *, html: bool = True) -> str:
    """«100₽ вместо 150₽ −33%». Без скидки — просто «150₽».

    Показывать обе цены обязательно: со скидкой цена на кнопке не сходится
    с ценой в разделе «Тарифы», и без пояснения это выглядит как ошибка
    бота, а не как акция.
    """
    full, price = int(full or 0), int(price or 0)
    if price >= full:
        return f'{price}₽'

    old = f'<s>{full}₽</s>' if html else strike(f'{full}₽')
    return f'{price}₽ вместо {old} −{discount_percent(full, price)}%'


async def price_line_for(c, user: dict) -> str:
    """«150₽ за месяц + 225₽/мес за устройства».

    Цена тарифа — со скидкой аудитории: на этом экране человек читает, сколько
    с него спишут при следующем продлении, и полная цена здесь была бы враньём.
    """
    vpn = user.get('vpn') or {}
    days = int(vpn.get('period') or 0)
    plan = await c.plans.by_days(days)
    rules = await c.topup.rules()

    full = int(plan['price']) if plan else 0
    price = (await c.discounts.price(user, plan)) if (plan and c.discounts) else full
    return price_line(price, days,
                      devices_price(int(vpn.get('hwidDeviceLimit') or 0), rules),
                      full_price=full)
