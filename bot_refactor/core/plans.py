"""Тарифы как данные, а не как if-цепочка.

Было (start.py):
    price, duration = (6, 1) if duration == '1day' else (150, 30) if ... else (3000, 1095)
    keyboard.row(InlineKeyboardButton(text='1 месяц + 🎁 - 150₽', ...))
    ... и то же самое ещё в 4 местах

Стало:
    for plan in await all_plans():
        kb.row(plan_button(plan, balance))

Цены, сроки, подарки и порядок кнопок редактируются в /admin → 💰 Тарифы.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from loader import db

plans_col = db['plans']

CACHE_TTL_SEC = 10.0
_cache: list[dict] = []
_loaded_at = 0.0

# Текущие «зашитые» тарифы — используются один раз при первом запуске.
DEFAULT_PLANS: list[dict[str, Any]] = [
    {'code': '1day',   'title': 'Ежедневная', 'days': 1,    'price': 6,    'order': 10,
     'gift_type': '',       'gift_count': 0, 'enabled': True},
    {'code': '1month', 'title': '1 месяц',    'days': 30,   'price': 150,  'order': 20,
     'gift_type': '1day',   'gift_count': 1, 'enabled': True},
    {'code': '3month', 'title': '3 месяца',   'days': 90,   'price': 375,  'order': 30,
     'gift_type': '1month', 'gift_count': 1, 'enabled': True},
    {'code': '3year',  'title': '3 года',     'days': 1095, 'price': 3000, 'order': 40,
     'gift_type': '1month', 'gift_count': 3, 'enabled': True},
]


def invalidate_plans() -> None:
    global _loaded_at
    _loaded_at = 0.0


async def seed_plans() -> None:
    """Вызывается один раз на старте приложения (on_startup)."""
    if await plans_col.count_documents({}) == 0:
        await plans_col.insert_many([dict(p, created_at=datetime.now()) for p in DEFAULT_PLANS])
    await plans_col.create_index('code', unique=True)
    invalidate_plans()


async def _load() -> list[dict]:
    global _cache, _loaded_at
    if _cache and (time.monotonic() - _loaded_at) < CACHE_TTL_SEC:
        return _cache

    docs = await plans_col.find({}, {'_id': 0}).sort('order', 1).to_list(length=None)
    _cache = docs or [dict(p) for p in DEFAULT_PLANS]
    _loaded_at = time.monotonic()
    return _cache


async def all_plans(only_enabled: bool = True) -> list[dict]:
    plans = await _load()
    return [p for p in plans if p.get('enabled', True)] if only_enabled else list(plans)


async def get_plan(code: str) -> dict | None:
    return next((p for p in await _load() if p.get('code') == code), None)


async def plan_by_days(days: int) -> dict | None:
    try:
        days = int(days)
    except (TypeError, ValueError):
        return None
    return next((p for p in await _load() if int(p.get('days', 0)) == days), None)


async def price_for_days(days: int) -> int:
    """Замена plan_base_price(): цена периода по количеству дней."""
    plan = await plan_by_days(days)
    return int(plan['price']) if plan else 0


async def plan_button_title(plan: dict) -> str:
    gift = ' + 🎁' if plan.get('gift_count') else ''
    return f'{plan["title"]}{gift} - {plan["price"]}₽'


async def devices_monthly_price(user: dict) -> int:
    """Цена платных устройств в месяц. Берёт цену и бесплатный лимит из настроек."""
    from core.settings import S

    limit = int((user.get('vpn') or {}).get('hwidDeviceLimit', 0) or 0)
    free = await S.int('price.devices_free_limit')
    per_device = await S.int('price.device_extra')
    return max(0, limit - free) * per_device


async def subscription_monthly_cost(user: dict) -> int:
    """Полная стоимость следующего списания: тариф + доп. устройства."""
    period = (user.get('vpn') or {}).get('period') or 0
    return await price_for_days(period) + await devices_monthly_price(user)
