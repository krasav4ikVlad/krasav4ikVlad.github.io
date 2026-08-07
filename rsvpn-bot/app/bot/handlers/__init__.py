"""Сборка роутеров бота.

Порядок важен: специфичные разделы раньше общих, fallback — последним,
иначе он перехватит сообщения, адресованные состояниям других разделов.
Админка подключается отдельно в factory.create_dispatcher.
"""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers import (bypass, common, devices, fallback, gifts, payments,
                              payouts, profile, promo, referrals, start, subscription,
                              support, trial)

SECTIONS = (common, start, profile, subscription, devices, bypass, payments,
            referrals, payouts, gifts, promo, trial, support, fallback)


def register(dp: Dispatcher) -> None:
    for section in SECTIONS:
        dp.include_router(section.create_router())
