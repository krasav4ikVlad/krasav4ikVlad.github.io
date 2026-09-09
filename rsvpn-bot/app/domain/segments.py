"""Сегменты пользователей: правила в одной таблице.

Раньше границы сегментов были зашиты в update_users_segments и продублированы
в трёх файлах кампаний (TRIAL_RANGES, EXPIRED_KEYS, списки строк в admin.py).
Здесь один источник: и расчёт сегмента, и подписи для админки, и фильтры
для кампаний берутся отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.content.emoji import e


@dataclass(frozen=True)
class Segment:
    code: str
    title: str
    group: str          # trial | active | expired | churned | other
    active: bool = False   # подписка сейчас работает

    # Группа и `active` — разные вещи, и именно поэтому флаг отдельный.
    # В группе trial лежат и те, у кого триал идёт прямо сейчас (подписка
    # работает), и те, у кого он закончился (не работает). Считать «активен
    # ли человек» по названию группы — значит промахнуться ровно на этих.


SEGMENTS: tuple[Segment, ...] = (
    Segment('new_trial_d0', f'{e("new")} Новые триал D0', 'trial', active=True),
    Segment('new_trial_d1', f'{e("new")} Новые триал D1', 'trial', active=True),
    Segment('new_trial_d2', f'{e("new")} Новые триал D2', 'trial', active=True),
    Segment('new_trial_d2_hot', f'{e("hot")} Новые триал D2 HOT', 'trial', active=True),
    Segment('new_trial_d3', f'{e("new")} Новые триал D3', 'trial', active=True),
    Segment('new_trial_d3_hot', f'{e("hot")} Новые триал D3 HOT', 'trial', active=True),
    Segment('trial', f'{e("trial")} Триал закончился', 'trial'),
    Segment('active_no_topup', f'{e("yellow")} Активные без пополнения', 'active', active=True),
    Segment('first_payment_active', f'{e("card")} Активные с 1 оплатой', 'active', active=True),
    Segment('active_paid', f'{e("green")} Активные платящие', 'active', active=True),
    Segment('expiring_3d', f'{e("hourglass")} Истекают до 3 дней', 'active', active=True),
    Segment('expired_1d', f'{e("renew")} Истекли 0–1 день', 'expired'),
    Segment('expired_3d', f'{e("renew")} Истекли 2–3 дня', 'expired'),
    Segment('expired_7d', f'{e("renew")} Истекли 4–7 дней', 'expired'),
    Segment('expired_14d', f'{e("renew")} Истекли 8–14 дней', 'expired'),
    Segment('expired_21d', f'{e("renew")} Истекли 15–21 день', 'expired'),
    Segment('expired_30d', f'{e("renew")} Истекли 22–30 дней', 'expired'),
    Segment('churned_45d', f'{e("skull")} Ушли 31–45 дней', 'churned'),
    Segment('churned_60d', f'{e("skull")} Ушли 46–60 дней', 'churned'),
    Segment('churned_90d', f'{e("skull")} Ушли 61–90 дней', 'churned'),
    Segment('churned_dead', f'{e("skull")} Ушли 90+ дней', 'churned'),
    Segment('inactive_no_sub', f'{e("white")} Без подписки и оплат', 'other'),
)

BY_CODE = {s.code: s for s in SEGMENTS}
BY_GROUP: dict[str, tuple[str, ...]] = {}
for _s in SEGMENTS:
    BY_GROUP[_s.group] = BY_GROUP.get(_s.group, ()) + (_s.code,)

# Все, у кого подписка сейчас не работает: закончилась, ушли давно или её
# не было вовсе. Собирается из флага, а не перечислением кодов, — новый
# сегмент попадёт сюда сам, и забыть его дописать не выйдет.
NO_ACTIVE: tuple[str, ...] = tuple(s.code for s in SEGMENTS if not s.active)


# ── аудитории: крупные наборы сегментов ─────────────────────────────────────
# Двадцать два сегмента хороши для автокампаний, но в админке ими неудобно
# целиться: «всем на триале» — это шесть кодов. Аудитория — один код на
# понятную группу людей. Один и тот же словарь у рассылки, скидок и сброса
# триала: иначе «на триале» в трёх местах означало бы три разных набора.
AUDIENCES: dict[str, tuple[str, tuple[str, ...]]] = {
    'all': (f'{e("channel")} Всем', ()),
    'trial': (f'{e("trial")} На триале', BY_GROUP.get('trial', ())),
    'active': (f'{e("green")} С активной подпиской', BY_GROUP.get('active', ())),
    # Шире, чем «истёкшие»: сюда же попадают закончившийся триал и те,
    # у кого подписки не было никогда.
    'no_active': (f'{e("cross")} Без активной подписки', NO_ACTIVE),
    'expired': (f'{e("renew")} Истёкшие', BY_GROUP.get('expired', ())),
    'churned': (f'{e("skull")} Давно ушедшие', BY_GROUP.get('churned', ())),
    'no_sub': (f'{e("white")} Без подписки и оплат', ('inactive_no_sub',)),
}


def audience_query(audience: str) -> dict:
    """Фильтр по сегментам для запроса в Mongo. Пустой набор = вся база."""
    codes = AUDIENCES.get(audience, ('', ()))[1]
    return {'growth.segment': {'$in': list(codes)}} if codes else {}


def audiences_of(segment: str) -> tuple[str, ...]:
    """В какие аудитории попадает сегмент. 'all' — всегда."""
    return ('all',) + tuple(code for code, (_, codes) in AUDIENCES.items()
                            if codes and segment in codes)

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


def trial_segment(days_since_join: int, hours_left: float | None) -> str:
    """Сегмент новичка на триале: по дню регистрации и остатку времени."""
    if days_since_join == 0:
        return 'new_trial_d0'
    if days_since_join == 1:
        return 'new_trial_d1'
    if days_since_join == 2:
        return ('new_trial_d2_hot' if hours_left is not None and hours_left <= 6
                else 'new_trial_d2')
    return ('new_trial_d3_hot' if hours_left is not None and hours_left <= 2
            else 'new_trial_d3')


# ── расчёт сегмента ─────────────────────────────────────────────────────────
AB_GROUPS = ('control', 'bonus_30', 'bonus_15')
TRIAL_AB_GROUPS = ('trial_control', 'trial_bonus_30', 'trial_bonus_50')
NEW_TRIAL_SEGMENTS = BY_GROUP.get('trial', ())


def determine(user: dict, now, choose=None) -> dict:
    """Сегмент пользователя и сопутствующие поля growth.*

    Перенос из utils._determine_segment. Порядок правил сохранён: приоритет
    сверху вниз, первый подошедший выигрывает. `choose` подменяется в тестах,
    чтобы A/B-группа не была случайной.
    """
    import random

    from app.core.time import parse_dt
    from app.domain.transactions import topup_stats

    choose = choose or random.choice
    user_data = user.get('user_data') or {}
    info = user.get('info') or {}
    vpn = user.get('vpn') or {}

    joined_at = parse_dt(user_data.get('date_joined'))
    expire_at = parse_dt(vpn.get('expireAt'))
    stats = topup_stats(info.get('transactions'))

    has_sub = bool((vpn.get('shortUuid') or '').strip())
    is_active = bool(has_sub and expire_at and expire_at > now)
    has_topup = stats['has_topup']

    days_since_join = max(0, (now - joined_at).days) if joined_at else None
    days_to_expire = ((expire_at - now).total_seconds() / 86400) if is_active else None
    hours_to_expire = days_to_expire * 24 if days_to_expire is not None else None
    days_since_expired = ((now - expire_at).days
                          if expire_at and expire_at <= now else None)

    if is_active and has_topup and days_to_expire is not None and days_to_expire <= 3:
        segment = 'expiring_3d'
    elif is_active and not has_topup and (days_since_join is None or days_since_join > 3):
        segment = 'active_no_topup'
    elif is_active and stats['topups_count'] == 1:
        segment = 'first_payment_active'
    elif is_active and stats['topups_count'] >= 2:
        segment = 'active_paid'
    elif not is_active and has_topup and days_since_expired is not None:
        segment = expired_segment(days_since_expired)
    elif not has_topup:
        if is_active and days_since_join is not None and days_since_join <= 3:
            segment = trial_segment(days_since_join, hours_to_expire)
        elif has_sub and days_since_expired is not None:
            segment = 'trial'
        else:
            segment = 'inactive_no_sub'
    else:
        segment = 'inactive_no_sub'

    # A/B-группа назначается один раз и дальше не меняется
    growth = user.get('growth') or {}
    ab_group = growth.get('ab_group')
    # 'used' — терминальное состояние: бонус новичка уже выдан. Без этой
    # проверки пересчёт сегментов возвращал человека в бонусную группу, и
    # второе пополнение в те же трое суток снова получало надбавку.
    if ab_group != 'used' and ab_group not in AB_GROUPS:
        ab_group = choose(AB_GROUPS) if segment in NEW_TRIAL_SEGMENTS else None

    trial_ab = growth.get('trial_ab_group')
    if trial_ab not in TRIAL_AB_GROUPS:
        trial_ab = choose(TRIAL_AB_GROUPS) if segment == 'trial' else None

    return {
        'segment': segment, 'segment_updated_at': now,
        'ab_group': ab_group, 'trial_ab_group': trial_ab,
        'has_sub': has_sub, 'is_active': is_active,
        'joined_at': joined_at, 'expire_at': expire_at,
        'days_since_join': days_since_join,
        'days_to_expire': round(days_to_expire, 2) if days_to_expire is not None else None,
        'hours_to_expire': round(hours_to_expire, 2) if hours_to_expire is not None else None,
        'days_since_expired': days_since_expired,
        'has_topup': has_topup,
        'topups_count': stats['topups_count'],
        'topups_total': stats['topups_total'],
        'first_topup_at': stats['first_topup_at'],
        'last_topup_at': stats['last_topup_at'],
        'balance': int(info.get('balance', 0) or 0),
    }
