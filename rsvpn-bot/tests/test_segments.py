"""Сегменты: правила отнесения и пересчёт по базе."""

from datetime import timedelta

import pytest

from app.core.time import now
from app.domain.segments import determine
from app.domain.transactions import extract, is_topup, topup_stats
from app.repositories.users import UsersRepository
from app.services.segments import SegmentService


def user(*, joined_days=0, expire_days=None, short='s-1', transactions=None, growth=None):
    return {
        'user_data': {'user_id': 1, 'date_joined': now() - timedelta(days=joined_days)},
        'info': {'balance': 0, 'transactions': transactions or []},
        'vpn': {'shortUuid': short,
                'expireAt': now() + timedelta(days=expire_days) if expire_days is not None else None},
        'growth': growth or {},
    }


TOPUP_OLD = [100, now(), 'Пополнение (wata)']
TOPUP_NEW = {'amount': 100, 'dt': now(), 'description': 'Пополнение (heleket)'}


# ── разбор транзакций ───────────────────────────────────────────────────────
def test_both_transaction_formats_are_understood():
    """Списки и словари лежат в базе вперемешку — оба формата рабочие."""
    assert extract(TOPUP_OLD)[0] == 100
    assert extract(TOPUP_NEW)[0] == 100
    assert is_topup(TOPUP_OLD) and is_topup(TOPUP_NEW)


def test_purchases_are_not_topups():
    assert is_topup([-150, now(), 'Покупка подписки']) is False
    assert is_topup({'amount': 150, 'dt': now(), 'description': 'Бонус за опрос'}) is False


def test_topup_stats_counts_both_formats():
    stats = topup_stats([TOPUP_OLD, TOPUP_NEW, [-150, now(), 'Покупка подписки']])
    assert stats['topups_count'] == 2 and stats['topups_total'] == 200


# ── правила сегментов ───────────────────────────────────────────────────────
def test_new_trial_by_day():
    assert determine(user(joined_days=0, expire_days=3), now())['segment'] == 'new_trial_d0'
    assert determine(user(joined_days=1, expire_days=2), now())['segment'] == 'new_trial_d1'
    assert determine(user(joined_days=2, expire_days=1), now())['segment'] == 'new_trial_d2'


def test_hot_segments_when_little_time_left():
    hot2 = determine(user(joined_days=2, expire_days=0.2), now())
    hot3 = determine(user(joined_days=3, expire_days=0.05), now())
    assert hot2['segment'] == 'new_trial_d2_hot'
    assert hot3['segment'] == 'new_trial_d3_hot'


def test_paying_active_segments():
    one = determine(user(joined_days=40, expire_days=20, transactions=[TOPUP_OLD]), now())
    two = determine(user(joined_days=40, expire_days=20,
                         transactions=[TOPUP_OLD, TOPUP_NEW]), now())
    assert one['segment'] == 'first_payment_active'
    assert two['segment'] == 'active_paid'


def test_expiring_soon_wins_over_active():
    result = determine(user(joined_days=40, expire_days=2, transactions=[TOPUP_OLD]), now())
    assert result['segment'] == 'expiring_3d'


def test_expired_paying_by_days():
    for days, expected in ((-1, 'expired_1d'), (-5, 'expired_7d'),
                           (-20, 'expired_21d'), (-100, 'churned_dead')):
        result = determine(user(joined_days=200, expire_days=days,
                                transactions=[TOPUP_OLD]), now())
        assert result['segment'] == expected, (days, result['segment'])


def test_trial_that_never_paid():
    result = determine(user(joined_days=30, expire_days=-10), now())
    assert result['segment'] == 'trial'


def test_user_without_subscription():
    result = determine(user(joined_days=30, expire_days=None, short=''), now())
    assert result['segment'] == 'inactive_no_sub'


def test_ab_group_is_assigned_once_and_kept():
    first = determine(user(joined_days=0, expire_days=3), now(), choose=lambda x: x[1])
    assert first['ab_group'] == 'bonus_30'

    again = determine(user(joined_days=0, expire_days=3, growth={'ab_group': 'control'}),
                      now(), choose=lambda x: x[1])
    assert again['ab_group'] == 'control'


def test_growth_fields_feed_the_campaigns():
    result = determine(user(joined_days=1, expire_days=2, transactions=[TOPUP_OLD]), now())
    assert result['has_topup'] is True
    assert result['topups_count'] == 1
    assert result['balance'] == 0
    assert result['days_since_join'] == 1


# ── пересчёт по базе ────────────────────────────────────────────────────────
async def test_service_updates_everyone(db, user_factory):
    await user_factory(**{'vpn.shortUuid': 's-1',
                          'vpn.expireAt': now() + timedelta(days=10),
                          'user_data.date_joined': now() - timedelta(days=40),
                          'info.transactions': [TOPUP_OLD]})
    await user_factory(**{'vpn.shortUuid': '', 'user_data.date_joined': now()})

    report = await SegmentService(UsersRepository(db['users'])).run()

    assert report.total == 2 and report.updated == 2
    first = await db['users'].find_one({'user_data.user_id': 1})
    assert first['growth']['segment'] == 'first_payment_active'


async def test_segment_change_is_recorded_in_history(db, user_factory):
    await user_factory(**{'vpn.shortUuid': '', 'growth.segment': 'active_paid',
                          'user_data.date_joined': now()})

    await SegmentService(UsersRepository(db['users'])).run()

    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['growth']['segment'] == 'inactive_no_sub'
    assert doc['growth_history'][-1]['from'] == 'active_paid'
