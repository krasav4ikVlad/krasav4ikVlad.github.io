"""Lifeline: перевод на запасной сервер и возврат после продления."""

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now, plus_days
from app.repositories.users import UsersRepository
from app.services.lifeline import LifelineService
from app.settings.service import SettingsService


class FakeVpn:
    def __init__(self, fail=False):
        self.calls: list[dict] = []
        self.fail = fail

    async def update_subscription(self, uuid, *, expire_at=None, squads=None, **kw):
        if self.fail:
            raise VpnPanelError('panel down')
        self.calls.append({'uuid': uuid, 'expire_at': expire_at, 'squads': squads})
        return {'uuid': uuid}


@pytest.fixture
def lifeline(db):
    vpn = FakeVpn()
    service = LifelineService(UsersRepository(db['users']),
                              SettingsService(db['bot_settings']), vpn)
    return service, vpn


async def test_moves_expired_user_to_backup_squad(db, user_factory, lifeline):
    service, vpn = lifeline
    user = await user_factory(**{'vpn.uuid': 'u-1',
                                 'vpn.activeInternalSquads': ['base', 'extra']})

    result = await service.on_expired(user)

    squad = await service.settings.get('lifeline.squad_uuid')
    assert result['note'] == 'moved'
    assert vpn.calls[0]['squads'] == [squad]

    saved = await db['users'].find_one({'vpn.uuid': 'u-1'})
    assert saved['vpn']['in_lifeline'] is True
    assert saved['vpn']['orig_squads'] == ['base', 'extra']


async def test_second_expiry_only_extends_grace(db, user_factory, lifeline):
    service, vpn = lifeline
    user = await user_factory(**{'vpn.uuid': 'u-1', 'vpn.in_lifeline': True,
                                 'vpn.activeInternalSquads': ['base']})

    assert (await service.on_expired(user))['note'] == 'grace_extended'


async def test_user_without_saved_squads_is_skipped(db, user_factory, lifeline):
    """Иначе человека некуда возвращать — он навсегда останется на одном сервере."""
    service, vpn = lifeline
    user = await user_factory(**{'vpn.uuid': 'u-1', 'vpn.activeInternalSquads': []})

    assert (await service.on_expired(user))['note'] == 'no_original_squads'
    assert vpn.calls == []


async def test_panel_failure_does_not_mark_user_moved(db, user_factory):
    service = LifelineService(UsersRepository(db['users']),
                              SettingsService(db['bot_settings']), FakeVpn(fail=True))
    user = await user_factory(**{'vpn.uuid': 'u-1', 'vpn.activeInternalSquads': ['base']})

    assert (await service.on_expired(user))['note'] == 'panel_error'
    saved = await db['users'].find_one({'vpn.uuid': 'u-1'})
    assert saved['vpn'].get('in_lifeline') is not True


async def test_feature_can_be_switched_off(db, user_factory, lifeline):
    service, vpn = lifeline
    await service.settings.set('lifeline.enabled', False)
    user = await user_factory(**{'vpn.uuid': 'u-1', 'vpn.activeInternalSquads': ['base']})

    assert (await service.on_expired(user))['note'] == 'disabled'
    assert vpn.calls == []


async def test_restore_returns_original_squads(db, user_factory, lifeline):
    service, vpn = lifeline
    await user_factory(**{'vpn.uuid': 'u-1', 'vpn.in_lifeline': True,
                          'vpn.orig_squads': ['base', 'extra'],
                          'vpn.expireAt': plus_days(30)})

    assert await service.restore(1) is True
    assert vpn.calls[-1]['squads'] == ['base', 'extra']

    saved = await db['users'].find_one({'user_data.user_id': 1})
    assert saved['vpn']['in_lifeline'] is False
    assert 'orig_squads' not in saved['vpn']


async def test_restore_skips_users_not_in_lifeline(db, user_factory, lifeline):
    service, vpn = lifeline
    await user_factory(**{'vpn.uuid': 'u-1'})
    assert await service.restore(1) is False
    assert vpn.calls == []


async def test_reconcile_restores_only_renewed(db, user_factory, lifeline):
    service, vpn = lifeline
    await user_factory(**{'vpn.uuid': 'renewed', 'vpn.in_lifeline': True,
                          'vpn.orig_squads': ['base'], 'vpn.expireAt': plus_days(10)})
    await user_factory(**{'vpn.uuid': 'still-expired', 'vpn.in_lifeline': True,
                          'vpn.orig_squads': ['base'], 'vpn.expireAt': now()})

    assert await service.reconcile() == 1
    assert vpn.calls[-1]['uuid'] == 'renewed'
