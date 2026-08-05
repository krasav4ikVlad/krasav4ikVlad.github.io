"""Плата за доп. устройства: попадание в нужный пакет, отключение при нехватке."""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now
from app.repositories.users import UsersRepository
from app.services.devices import DeviceBillingService
from app.settings.service import SettingsService


class FakeVpn:
    def __init__(self, fail=False):
        self.calls: list[dict] = []
        self.fail = fail

    async def update_subscription(self, uuid, *, device_limit=None, **kw):
        if self.fail:
            raise VpnPanelError('panel down')
        self.calls.append({'uuid': uuid, 'device_limit': device_limit})
        return {}


@pytest.fixture
def billing(db):
    vpn = FakeVpn()
    service = DeviceBillingService(UsersRepository(db['users']),
                                   SettingsService(db['bot_settings']), vpn)
    return service, vpn


def package(pid: str, amount: int, due_hours: int, price=75, active=True):
    return {'id': pid, 'amount': amount, 'pricePerDevice': price, 'active': active,
            'nextChargeAt': now() + timedelta(hours=due_hours)}


async def test_charges_when_due(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'info.balance': 200, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 2, -1)]})

    report = await service.run()

    assert report.charged == 1 and report.amount == 150
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 50


async def test_does_not_charge_before_the_date(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'info.balance': 200, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 1, 48)]})

    report = await service.run()
    assert report.charged == 0 and report.skipped == 1


async def test_charge_moves_the_right_package(db, user_factory, billing):
    """Ключевой случай: два пакета, платить пора только второму.

    Со старым фильтром (три отдельных условия по массиву) позиционный `$`
    попадал в первый пакет и сдвигал дату не тому, кому списали.
    """
    service, vpn = billing
    paid_later = package('p1', 1, 240)      # платить ещё не скоро
    due_now = package('p2', 2, -1)          # пора платить
    await user_factory(**{'info.balance': 500, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [paid_later, due_now]})

    await service.run()

    user = await db['users'].find_one({'user_data.user_id': 1})
    first, second = user['vpn']['extraDevices']
    assert first['nextChargeAt'] == paid_later['nextChargeAt']          # не тронут
    assert second['nextChargeAt'] > due_now['nextChargeAt']             # сдвинут на месяц
    assert user['info']['balance'] == 350                               # списано 2×75


async def test_deactivates_package_when_no_money(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'info.balance': 10, 'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 4,
                          'vpn.extraDevices': [package('p1', 2, -1)]})

    report = await service.run()

    assert report.deactivated == 1
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['vpn']['extraDevices'][0]['active'] is False
    assert user['vpn']['hwidDeviceLimit'] == 2                # базовый лимит из настроек
    assert vpn.calls[-1]['device_limit'] == 2


async def test_base_limit_comes_from_settings(db, user_factory, billing):
    service, vpn = billing
    await service.settings.set('price.devices_free_limit', 3)
    await user_factory(**{'info.balance': 0, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 1, -1)]})

    await service.run()

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['vpn']['hwidDeviceLimit'] == 3


async def test_panel_failure_keeps_package_active(db, user_factory, billing):
    """Иначе в базе лимит уменьшен, а в панели нет — устройства разъезжаются."""
    service, _ = billing
    service.vpn = FakeVpn(fail=True)
    await user_factory(**{'info.balance': 0, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 1, -1)]})

    report = await service.run()

    assert report.deactivated == 0
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['vpn']['extraDevices'][0]['active'] is True


async def test_inactive_packages_are_skipped(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'info.balance': 500, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 2, -1, active=False)]})

    report = await service.run()
    assert report.charged == 0
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 500


async def test_feature_toggle_stops_billing(db, user_factory, billing):
    service, vpn = billing
    await service.settings.set('features.devices_enabled', False)
    await user_factory(**{'info.balance': 500, 'vpn.uuid': 'u-1',
                          'vpn.extraDevices': [package('p1', 2, -1)]})

    report = await service.run()
    assert report.charged == 0


# ── покупка и отвязка ───────────────────────────────────────────────────────
async def test_buying_devices_charges_and_raises_limit(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'info.balance': 300, 'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 2})

    new_limit = await service.add(1, 2)

    assert new_limit == 4 and vpn.calls[-1]['device_limit'] == 4
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 150
    assert user['vpn']['hwidDeviceLimit'] == 4
    assert user['vpn']['extraDevices'][0]['amount'] == 2


async def test_buying_without_money_is_refused(db, user_factory, billing):
    from app.core.errors import NotEnoughBalance
    service, vpn = billing
    await user_factory(**{'info.balance': 10, 'vpn.uuid': 'u-1'})

    with pytest.raises(NotEnoughBalance):
        await service.add(1, 2)

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 10 and vpn.calls == []


async def test_panel_failure_returns_money(db, user_factory, billing):
    service, _ = billing
    service.vpn = FakeVpn(fail=True)
    await user_factory(**{'info.balance': 300, 'vpn.uuid': 'u-1'})

    with pytest.raises(VpnPanelError):
        await service.add(1, 2)

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 300
    assert not user['vpn'].get('extraDevices')


async def test_unbind_removes_device_from_both_profiles(db, user_factory, billing):
    service, vpn = billing
    vpn.deleted = []

    async def delete_device(uuid, hwid):
        vpn.deleted.append((uuid, hwid))
        return True

    vpn.delete_device = delete_device
    await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bypass'})

    assert await service.unbind(1, 'hwid-1') is True
    assert vpn.deleted == [('main', 'hwid-1'), ('bypass', 'hwid-1')]
