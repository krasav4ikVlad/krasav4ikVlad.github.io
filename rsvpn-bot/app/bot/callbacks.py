"""Типизированные callback_data вместо разбора строк.

Было: call.data.endswith(':buy_sub') и call.data.split(':')[1] в 40 местах —
опечатка ловится только в проде. Стало: фабрики aiogram, где поля объявлены,
а фильтр по ним матчится до входа в хендлер.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Menu(CallbackData, prefix='m'):
    """Навигация по разделам: Menu(screen='profile')."""
    screen: str
    arg: str = ''


class Plan(CallbackData, prefix='plan'):
    action: str          # buy | change
    code: str


class Devices(CallbackData, prefix='dev'):
    action: str          # add | remove | info | unbind
    value: str = ''


class Payment(CallbackData, prefix='pay'):
    provider: str
    amount: int = 0


class Promo(CallbackData, prefix='promo'):
    action: str


class Admin(CallbackData, prefix='adm'):
    act: str
    a: str = ''
    b: str = ''
