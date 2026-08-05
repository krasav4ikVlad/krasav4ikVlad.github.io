"""Сборка роутеров бота.

Порядок важен: специфичные разделы раньше общих, fallback — последним,
иначе он перехватит сообщения, адресованные состояниям других разделов.
Админка подключается отдельно в factory.create_dispatcher.
"""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers import (bypass, devices, fallback, gifts, payments, profile,
                              promo, referrals, start, subscription, support)

SECTIONS = (start, profile, subscription, devices, bypass, payments,
            referrals, gifts, promo, support, fallback)


def register(dp: Dispatcher) -> None:
    for section in SECTIONS:
        dp.include_router(section.create_router())
