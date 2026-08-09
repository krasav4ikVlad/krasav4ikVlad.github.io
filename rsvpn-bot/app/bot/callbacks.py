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


class Payout(CallbackData, prefix='po'):
    """Способы вывода: список, черновик, выбор, заявка."""
    action: str          # methods | add | type | field | clear | save | view | delete
                         # | menu | pick | order
    value: str = ''


class PayoutAdmin(CallbackData, prefix='poa'):
    """Решение по заявке — кнопки под карточкой в админ-чате."""
    action: str          # balance | paid | reject | reject_ask
    user_id: int
    reason: str = ''


class Admin(CallbackData, prefix='adm'):
    act: str
    a: str = ''
    b: str = ''


class Server(CallbackData, prefix='srv'):
    """Личный сервер: витрина, покупка, участники."""
    action: str          # shop | buy | order | open | invite | accept | decline
                         # | members | kick | leave | stats | link
    value: str = ''


class ServerAdmin(CallbackData, prefix='srva'):
    """Заявка на личный сервер — кнопки под карточкой в админ-чате."""
    action: str          # give | reject
    server_id: str
