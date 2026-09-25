"""Сводка по реферальной программе.

Считается из того, что уже накапливает TopupService (`info.ref_stats`), —
отдельных счётчиков заводить не нужно. Модуль чистый, поэтому деления
«средний чек» и «доход с активного друга» проверяются тестом, а не глазами
на боевых данных: именно там легко получить деление на ноль.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferralStats:
    invited: int = 0            # всего перешло по ссылке
    active: int = 0             # из них хоть раз пополнивших
    payments: int = 0           # сколько всего пополнений сделали друзья
    turnover: int = 0           # на какую сумму
    earned: int = 0             # сколько из этого начислено нам
    withdrawable: int = 0       # доступно к выводу сейчас

    @property
    def active_percent(self) -> int:
        return round(self.active * 100 / self.invited) if self.invited else 0

    @property
    def average_payment(self) -> int:
        return round(self.turnover / self.payments) if self.payments else 0

    @property
    def per_active_friend(self) -> int:
        return round(self.earned / self.active) if self.active else 0


def referral_stats(raw: dict | None) -> ReferralStats:
    stats = raw or {}

    def count(key: str) -> int:
        value = stats.get(key)
        return len(value) if isinstance(value, (list, tuple, set)) else 0

    def number(key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return ReferralStats(
        invited=count('referrals'),
        # платящих может оказаться больше, чем приглашённых: список рефералов
        # в старых документах местами обрезан, а начисления шли
        active=min(count('paying_referrals'), count('referrals')) or count('paying_referrals'),
        payments=number('payments_count'),
        turnover=number('turnover_total'),
        earned=number('earned_total'),
        withdrawable=number('withdrawable'),
    )
