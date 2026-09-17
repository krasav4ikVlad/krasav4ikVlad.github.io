"""Заявки на вывод: минимум, кулдаун, двойной клик, решения админа."""

from datetime import timedelta

import pytest

from app.core.time import now
from app.repositories.users import UsersRepository
from app.services.payouts import PayoutService
from app.settings.service import SettingsService


@pytest.fixture
def payouts(db):
    return PayoutService(UsersRepository(db['users']), SettingsService(db['bot_settings']))


async def test_request_below_minimum_is_refused(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 100})
    result = await payouts.request(1)
    assert not result.ok and result.reason == 'below_min'


async def test_request_is_accepted(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 900})
    result = await payouts.request(1)

    assert result.ok and result.amount == 900
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['ref_stats']['pending_payout_active'] is True


async def test_double_click_creates_one_request(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 900})

    first = await payouts.request(1)
    second = await payouts.request(1)

    assert first.ok and not second.ok and second.reason == 'pending'


async def test_cooldown_between_requests(db, user_factory, payouts):
    await user_factory(**{
        'info.ref_stats.withdrawable': 900,
        'info.ref_stats.last_payout_request_at': now() - timedelta(hours=2)})

    result = await payouts.request(1)
    assert not result.ok and result.reason == 'cooldown' and result.wait_hours > 21


async def test_cooldown_expires(db, user_factory, payouts):
    await user_factory(**{
        'info.ref_stats.withdrawable': 900,
        'info.ref_stats.last_payout_request_at': now() - timedelta(hours=30)})

    assert (await payouts.request(1)).ok is True


async def test_to_balance_moves_money_once(db, user_factory, payouts):
    await user_factory(**{'info.balance': 100, 'info.ref_stats.withdrawable': 900})
    await payouts.request(1)

    moved = await payouts.to_balance(1, admin_id=42)
    again = await payouts.to_balance(1, admin_id=42)

    assert moved == 900 and again == 0
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 1000
    assert user['info']['ref_stats']['withdrawable'] == 0
    assert user['info']['ref_stats']['pending_payout_active'] is False


async def test_paid_externally_clears_referral_balance(db, user_factory, payouts):
    await user_factory(**{'info.balance': 100, 'info.ref_stats.withdrawable': 900})
    await payouts.request(1)

    assert await payouts.paid_externally(1, admin_id=42) == 900
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['ref_stats']['withdrawable'] == 0
    assert user['info']['balance'] == 100          # на баланс бота не переводили


async def test_reject_keeps_the_money(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 900})
    await payouts.request(1)

    text = await payouts.reject(1, 'data', admin_id=42)

    assert 'реквизит' in text
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['ref_stats']['withdrawable'] == 900
    assert user['info']['ref_stats']['pending_payout_active'] is False
    assert user['info']['ref_stats']['payout_history'][-1]['action'] == 'rejected'


async def test_new_request_possible_after_rejection(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 900})
    await payouts.request(1)
    await payouts.reject(1, 'form', admin_id=42)
    await payouts.settings.set('payout.cooldown_hours', 0)

    assert (await payouts.request(1)).ok is True


async def test_payouts_can_be_disabled(db, user_factory, payouts):
    await user_factory(**{'info.ref_stats.withdrawable': 900})
    await payouts.settings.set('features.payouts_enabled', False)
    assert (await payouts.request(1)).reason == 'disabled'
