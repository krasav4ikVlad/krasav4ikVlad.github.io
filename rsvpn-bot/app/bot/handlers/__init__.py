"""Сборка роутеров бота.

Порядок важен: специфичные разделы раньше общих, fallback — последним,
иначе он перехватит сообщения, адресованные состояниям других разделов.
Админка подключается отдельно в factory.create_dispatcher.
"""

from __future__ import annotations

from aiogram import Dispatcher

from app.bot.handlers import (bypass, common, devices, fallback, gifts,
                              maintenance, payments, payouts, private_servers,
                              profile, promo, referrals, start, subscription,
                              support, trial)

# Сапёр — раньше остальных: во время техработ перехватчик пропускает только
# его, и роутер должен быть на месте независимо от того, что там дальше.
SECTIONS = (maintenance, common, start, profile, subscription, devices, bypass,
            payments, referrals, payouts, private_servers, gifts, promo, trial,
            support, fallback)


def register(dp: Dispatcher) -> None:
    for section in SECTIONS:
        dp.include_router(section.create_router())
