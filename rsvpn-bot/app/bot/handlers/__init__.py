"""Сборка всех роутеров бота в одном месте.

Порядок важен: специфичные разделы раньше общих, fallback — последним.
Админка подключается отдельно в factory.create_dispatcher (у неё фабрика роутера).
"""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers import (bypass, devices, fallback, gifts, payments, profile,
                              promo, referrals, start, subscription, support)

ROUTERS = (
    start.router,
    profile.router,
    subscription.router,
    devices.router,
    bypass.router,
    payments.router,
    referrals.router,
    gifts.router,
    promo.router,
    support.router,
    fallback.router,   # всегда последним
)


def register(dp: Dispatcher) -> None:
    for router in ROUTERS:
        dp.include_router(router)
