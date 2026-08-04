"""Чистая арифметика денег. Без БД, без aiogram, без await.

Смысл слоя: любую денежную формулу можно проверить тестом за миллисекунду,
не поднимая Mongo и не подделывая Telegram. Всё, что сюда попадает, приходит
параметрами — сервис читает настройки и передаёт их сюда.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PricingRules:
    """Снимок настроек на момент расчёта."""
    device_price: int = 75
    free_devices: int = 2
    topup_bonus_rate: float = 0.0
    referral_rate: float = 0.30
    sleeping_discount: float = 0.40
    gateway_fee_rate: float = 0.05


def devices_price(device_limit: int, rules: PricingRules) -> int:
    """Плата за устройства сверх бесплатного лимита."""
    return max(0, device_limit - rules.free_devices) * rules.device_price


def subscription_price(plan_price: int, device_limit: int, rules: PricingRules,
                       discount: float = 0.0) -> int:
    """Полная стоимость списания: тариф + устройства, минус скидка."""
    total = plan_price + devices_price(device_limit, rules)
    return max(0, round(total * (1 - discount)))


def topup_credit(amount: int, rules: PricingRules, extra_rate: float = 0.0) -> tuple[int, int]:
    """Сколько зачислить за пополнение. Возвращает (итого, из них бонус)."""
    bonus = int(amount * (rules.topup_bonus_rate + extra_rate))
    return amount + bonus, bonus


def referral_reward(friend_topup: int, rules: PricingRules) -> int:
    return int(friend_topup * rules.referral_rate)


def net_from_gross(paid: int, rules: PricingRules) -> int:
    """Сколько зачислять, если провайдер берёт комиссию сверху (WATA)."""
    return int(round(paid / (1 + rules.gateway_fee_rate)))


def missing_amount(price: int, balance: int) -> int:
    return max(0, price - balance)


def days_for_balance(balance: int, daily_price: int) -> int:
    return balance // daily_price if daily_price > 0 else 0
