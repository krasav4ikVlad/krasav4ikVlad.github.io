"""Сегменты пользователей: правила в одной таблице.

Раньше границы сегментов были зашиты в update_users_segments и продублированы
в трёх файлах кампаний (TRIAL_RANGES, EXPIRED_KEYS, списки строк в admin.py).
Здесь один источник: и расчёт сегмента, и подписи для админки, и фильтры
для кампаний берутся отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    code: str
    title: str
    group: str  # trial | active | expired | churned | other


SEGMENTS: tuple[Segment, ...] = (
    Segment('new_trial_d0', '🆕 Новые триал D0', 'trial'),
    Segment('new_trial_d1', '🆕 Новые триал D1', 'trial'),
    Segment('new_trial_d2', '🆕 Новые триал D2', 'trial'),
    Segment('new_trial_d2_hot', '🔥 Новые триал D2 HOT', 'trial'),
    Segment('new_trial_d3', '🆕 Новые триал D3', 'trial'),
    Segment('new_trial_d3_hot', '🔥 Новые триал D3 HOT', 'trial'),
    Segment('trial', '🧪 Триал закончился', 'trial'),
    Segment('active_no_topup', '🟡 Активные без пополнения', 'active'),
    Segment('first_payment_active', '💳 Активные с 1 оплатой', 'active'),
    Segment('active_paid', '🟢 Активные платящие', 'active'),
    Segment('expiring_3d', '⏳ Истекают до 3 дней', 'active'),
    Segment('expired_1d', '🔁 Истекли 0–1 день', 'expired'),
    Segment('expired_3d', '🔁 Истекли 2–3 дня', 'expired'),
    Segment('expired_7d', '🔁 Истекли 4–7 дней', 'expired'),
    Segment('expired_14d', '🔁 Истекли 8–14 дней', 'expired'),
    Segment('expired_21d', '🔁 Истекли 15–21 день', 'expired'),
    Segment('expired_30d', '🔁 Истекли 22–30 дней', 'expired'),
    Segment('churned_45d', '💀 Ушли 31–45 дней', 'churned'),
    Segment('churned_60d', '💀 Ушли 46–60 дней', 'churned'),
    Segment('churned_90d', '💀 Ушли 61–90 дней', 'churned'),
    Segment('churned_dead', '💀 Ушли 90+ дней', 'churned'),
    Segment('inactive_no_sub', '⚪ Без подписки и оплат', 'other'),
)

BY_CODE = {s.code: s for s in SEGMENTS}
BY_GROUP: dict[str, tuple[str, ...]] = {}
for _s in SEGMENTS:
    BY_GROUP[_s.group] = BY_GROUP.get(_s.group, ()) + (_s.code,)

# Границы «сколько дней назад истекла подписка» → сегмент.
# Первый подходящий сверху вниз.
EXPIRED_BOUNDS: tuple[tuple[float, str], ...] = (
    (1, 'expired_1d'),
    (3, 'expired_3d'),
    (7, 'expired_7d'),
    (14, 'expired_14d'),
    (21, 'expired_21d'),
    (30, 'expired_30d'),
    (45, 'churned_45d'),
    (60, 'churned_60d'),
    (90, 'churned_90d'),
    (float('inf'), 'churned_dead'),
)


def expired_segment(days_since_expire: float) -> str:
    for limit, code in EXPIRED_BOUNDS:
        if days_since_expire <= limit:
            return code
    return 'churned_dead'


def trial_segment(hours_since_join: float, hours_left: float | None) -> str:
    """Сегмент новичка на триале: по возрасту регистрации и остатку времени."""
    if hours_left is not None and hours_left <= 2:
        return 'new_trial_d3_hot'
    if hours_left is not None and hours_left <= 6:
        return 'new_trial_d2_hot'
    if hours_since_join < 24:
        return 'new_trial_d0'
    if hours_since_join < 48:
        return 'new_trial_d1'
    if hours_since_join < 60:
        return 'new_trial_d2'
    return 'new_trial_d3'
