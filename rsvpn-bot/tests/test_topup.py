"""Зачисление денег: идемпотентность, бонусы, рефералка."""

import pytest

from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services.topup import TopupService
from app.settings.service import SettingsService


@pytest.fixture
def topup(db):
    return TopupService(
        UsersRepository(db['users']),
        PaymentsRepository(db['payments']),
        SettingsService(db['bot_settings']),
    )


async def balance(db, user_id: int) -> int:
    doc = await db['users'].find_one({'user_data.user_id': user_id})
    return doc['info']['balance']


async def test_credits_amount_with_bonus(db, user_factory, topup):
    await user_factory()
    result = await topup.process(provider='wata', txid='t1', amount=100, user_id=1)

    # бонус за пополнение по умолчанию 20%
    assert result['status'] == 'ok' and result['bonus'] == 20
    assert await balance(db, 1) == 120


async def test_duplicate_webhook_credits_once(db, user_factory, topup):
    await user_factory()
    first = await topup.process(provider='wata', txid='same', amount=100, user_id=1)
    second = await topup.process(provider='wata', txid='same', amount=100, user_id=1)

    assert first['status'] == 'ok' and second['status'] == 'duplicate'
    assert await balance(db, 1) == 120


async def test_bonus_can_be_switched_off_from_admin(db, user_factory, topup):
    await user_factory()
    await topup.settings.set('bonus.topup_enabled', False)

    result = await topup.process(provider='wata', txid='t2', amount=100, user_id=1)
    assert result['bonus'] == 0
    assert await balance(db, 1) == 100


async def test_tribute_gets_extra_percent(db, user_factory, topup):
    await user_factory()
    result = await topup.process(provider='tribute', txid='t3', amount=100, user_id=1)
    assert result['bonus'] == 25          # 20% обычный + 5% за Tribute


async def test_personal_multiplier_is_consumed_once(db, user_factory, topup):
    """Раньше cards_ru считал этот бонус сам и передавал уже увеличенную сумму,
    из-за чего сверху накручивался ещё и общий бонус."""
    await user_factory(**{'info.bonus_multiplier': 0.5})

    first = await topup.process(provider='cards_ru', txid='p1', amount=100, user_id=1)
    second = await topup.process(provider='cards_ru', txid='p2', amount=100, user_id=1)

    assert first['bonus'] == 70           # 20% + персональные 50%
    assert second['bonus'] == 20          # множитель израсходован
    assert await balance(db, 1) == 170 + 120


async def test_referrer_gets_percent_of_base_amount(db, user_factory, topup):
    await user_factory()                                   # user_id=1 — реферер
    await user_factory(**{'user_data.referrer': 1})        # user_id=2 — друг

    result = await topup.process(provider='wata', txid='r1', amount=500, user_id=2)

    assert result['referral_reward'] == 150                # 30% от 500, не от 600
    referrer = await db['users'].find_one({'user_data.user_id': 1})
    assert referrer['info']['ref_stats']['withdrawable'] == 150
    assert referrer['info']['ref_stats']['paying_referrals'] == [2]


async def test_referrals_can_be_disabled(db, user_factory, topup):
    await user_factory()
    await user_factory(**{'user_data.referrer': 1})
    await topup.settings.set('features.referrals_enabled', False)

    result = await topup.process(provider='wata', txid='r2', amount=500, user_id=2)
    assert result['referral_reward'] == 0


async def test_ab_bonus_applies_once_to_new_trial(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'new_trial_d2', 'growth.ab_group': 'bonus_30'})

    first = await topup.process(provider='wata', txid='ab1', amount=100, user_id=1)
    second = await topup.process(provider='wata', txid='ab2', amount=100, user_id=1)

    assert first['ab_bonus'] == 30
    assert second['ab_bonus'] == 0
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['growth']['ab_group'] == 'used'
    assert user['campaigns']['converted_from'] == 'bonus_30'


async def test_ab_bonus_not_given_outside_new_trial(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'active_paid', 'growth.ab_group': 'bonus_30'})
    result = await topup.process(provider='wata', txid='ab3', amount=100, user_id=1)
    assert result['ab_bonus'] == 0


async def test_campaign_conversion_is_recorded(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'expired_7d', 'campaigns.expired_7d_sent': 'x'})
    await topup.process(provider='wata', txid='c1', amount=200, user_id=1)

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['campaigns']['expired_converted_from'] == 'expired_7d'
    assert user['campaigns']['expired_converted_amount'] == 200


async def test_unknown_user_does_not_crash(db, topup):
    result = await topup.process(provider='wata', txid='u1', amount=100, user_id=999)
    assert result['status'] == 'user_not_found'


async def test_bad_input_is_rejected(db, user_factory, topup):
    await user_factory()
    assert (await topup.process(provider='wata', txid='x', amount=0, user_id=1))['status'] == 'bad_amount'
    assert (await topup.process(provider='wata', txid='', amount=10, user_id=1))['status'] == 'no_txid'
    assert (await topup.process(provider='wata', txid='y', amount=10, user_id=None))['status'] == 'unknown_user'
