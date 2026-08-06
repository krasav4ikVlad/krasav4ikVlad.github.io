"""Строка «Плата за подписку» — одна на все экраны.

Формат взят из старого `make_price_line`: пользователю важно видеть цену
тарифа за его период и отдельно ежемесячную плату за устройства, а не одно
итоговое число (по нему непонятно, за что списывают).

Живёт в screens/, а не в domain/, потому что читает тариф и настройки;
сама арифметика по-прежнему в domain/pricing.py.
"""

from __future__ import annotations

from app.bot.screens.profile import price_line
from app.domain.pricing import devices_price


async def price_line_for(c, user: dict) -> str:
    """«150₽ за месяц + 225₽/мес за устройства»."""
    vpn = user.get('vpn') or {}
    days = int(vpn.get('period') or 0)
    plan = await c.plans.by_days(days)
    rules = await c.topup.rules()
    return price_line(int(plan['price']) if plan else 0, days,
                      devices_price(int(vpn.get('hwidDeviceLimit') or 0), rules))
