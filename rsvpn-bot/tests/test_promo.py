"""Промокоды: одна активация на пользователя, лимиты, откат при сбое."""

import pytest

from app.core.errors import VpnPanelError
from app.repositories.users import UsersRepository
from app.services.promo import PromoService, normalize_code
from app.settings.service import SettingsService


class FakeVpn:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def update_subscription(self, uuid, *, traffic_bytes=None, **kw):
        if self.fail:
            raise VpnPanelError('panel down')
        self.calls.append({'uuid': uuid, 'traffic_bytes': traffic_bytes})
        return {}


@pytest.fixture
async def promo(db):
    service = PromoService(UsersRepository(db['users']), db['promo_codes'],
                           db['promo_usages'], SettingsService(db['bot_settings']),
                           FakeVpn())
    await service.ensure_indexes()
    return service


async def add_code(db, code='WELCOME100', **fields):
    doc = {'code': code, 'reward_type': 'balance', 'reward_value': 100,
           'max_uses': 0, 'used_count': 0, 'is_active': True, 'expires_at': None, **fields}
    await db['promo_codes'].insert_one(doc)
    return doc


def test_code_is_normalized():
    assert normalize_code(' welcome-100 ') == 'WELCOME-100'
    assert normalize_code('пРомо!!100') == '100'


async def test_balance_promo_credits_once(db, user_factory, promo):
    await user_factory()
    await add_code(db)

    first = await promo.redeem(1, 'welcome100')
    second = await promo.redeem(1, 'WELCOME100')

    assert first.ok and first.reward == '+100₽'
    assert not second.ok and second.reason == 'used'

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 100


async def test_activation_limit_is_enforced(db, user_factory, promo):
    await user_factory()
    await user_factory()
    await add_code(db, max_uses=1)

    assert (await promo.redeem(1, 'WELCOME100')).ok is True
    second = await promo.redeem(2, 'WELCOME100')

    assert not second.ok and second.reason == 'limit'
    # заявка второго пользователя откачена, счётчик не раздут
    code = await db['promo_codes'].find_one({'code': 'WELCOME100'})
    assert code['used_count'] == 1
    assert await db['promo_usages'].count_documents({'user_id': 2}) == 0


async def test_unknown_and_inactive_codes(db, user_factory, promo):
    await user_factory()
    await add_code(db, code='OFF', is_active=False)

    assert (await promo.redeem(1, 'NOPE')).reason == 'not_found'
    assert (await promo.redeem(1, 'OFF')).reason == 'not_found'


async def test_traffic_promo_requires_bypass(db, user_factory, promo):
    await user_factory()
    await add_code(db, code='GB10', reward_type='traffic_gb', reward_value=10)

    result = await promo.redeem(1, 'GB10')

    assert not result.ok and result.reason == 'no_bypass'
    # откат: код можно будет активировать снова, счётчик не увеличен
    code = await db['promo_codes'].find_one({'code': 'GB10'})
    assert code['used_count'] == 0
    assert await db['promo_usages'].count_documents({}) == 0


async def test_traffic_promo_adds_gigabytes(db, user_factory, promo):
    await user_factory(**{'vpn.bypass_uuid': 'bp-1', 'vpn.bypass_trafficLimitBytes': 1024 ** 3})
    await add_code(db, code='GB10', reward_type='traffic_gb', reward_value=10)

    result = await promo.redeem(1, 'GB10')

    assert result.ok and result.reward == '+10 Гб'
    assert promo.vpn.calls[0]['traffic_bytes'] == 11 * 1024 ** 3


async def test_panel_failure_rolls_everything_back(db, user_factory, promo):
    promo.vpn.fail = True
    await user_factory(**{'vpn.bypass_uuid': 'bp-1'})
    await add_code(db, code='GB10', reward_type='traffic_gb', reward_value=10)

    result = await promo.redeem(1, 'GB10')

    assert not result.ok and result.reason == 'panel_error'
    assert await db['promo_usages'].count_documents({}) == 0
    code = await db['promo_codes'].find_one({'code': 'GB10'})
    assert code['used_count'] == 0


async def test_promo_can_be_disabled(db, user_factory, promo):
    await user_factory()
    await add_code(db)
    await promo.settings.set('features.promo_enabled', False)

    assert (await promo.redeem(1, 'WELCOME100')).reason == 'disabled'
