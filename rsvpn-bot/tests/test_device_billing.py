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


def with_devices(vpn, by_uuid: dict[str, list[dict]]):
    """Устройства живут только в панели — вот она."""
    vpn.deleted = []

    async def devices(uuid):
        return by_uuid.get(uuid, [])

    async def delete_device(uuid, hwid):
        vpn.deleted.append((uuid, hwid))
        return True

    vpn.devices = devices
    vpn.delete_device = delete_device
    return vpn


async def test_unbind_goes_only_to_the_subscription_that_has_the_device(db, user_factory,
                                                                        billing):
    """Раньше удаление слалось в обе подписки, и «чужая» отвечала 404 A204 —
    в логах это выглядело ошибкой при каждой успешной отвязке."""
    service, vpn = billing
    with_devices(vpn, {'main': [{'hwid': 'hwid-1'}], 'bypass': [{'hwid': 'hwid-2'}]})
    await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bypass'})

    assert await service.unbind(1, 'hwid-2') is True
    assert vpn.deleted == [('bypass', 'hwid-2')]


async def test_unbind_all_covers_both_subscriptions(db, user_factory, billing):
    service, vpn = billing
    with_devices(vpn, {'main': [{'hwid': 'a'}, {'hwid': 'b'}], 'bypass': [{'hwid': 'c'}]})
    await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bypass'})

    assert await service.unbind_all(1) == 3
    assert vpn.deleted == [('main', 'a'), ('main', 'b'), ('bypass', 'c')]


async def test_unbind_all_does_not_touch_the_limit(db, user_factory, billing):
    """Освобождаются слоты, а не отменяется оплата за устройства."""
    service, vpn = billing
    with_devices(vpn, {'main': [{'hwid': 'a'}]})
    await user_factory(**{'vpn.uuid': 'main', 'vpn.hwidDeviceLimit': 5})

    await service.unbind_all(1)

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['vpn']['hwidDeviceLimit'] == 5


async def test_unbind_all_without_devices_is_harmless(db, user_factory, billing):
    service, vpn = billing
    with_devices(vpn, {})
    await user_factory(**{'vpn.uuid': 'main'})

    assert await service.unbind_all(1) == 0


async def test_unreachable_panel_does_not_break_the_list(db, user_factory, billing):
    service, vpn = billing

    async def devices(uuid):
        raise VpnPanelError('панель молчит')

    vpn.devices = devices
    await user_factory(**{'vpn.uuid': 'main'})

    assert await service.bound(1) == []


# ── боевые данные: лимит и пакеты часто расходятся ──────────────────────────
async def test_limit_drops_even_without_packages(db, user_factory, billing):
    """Лимит подняли из панели, пакетов в документе нет — кнопка обязана работать.

    Раньше новый лимит считался как «база + сумма пакетов»: при пустом списке
    лимит 17 так и оставался 17, и человек не мог его уменьшить вообще.
    """
    service, vpn = billing
    await user_factory(**{'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 17})

    assert await service.remove(1, 1) == 16
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['vpn']['hwidDeviceLimit'] == 16


async def test_limit_is_not_reset_to_the_sum_of_packages(db, user_factory, billing):
    """Пакеты неполные — лимит уменьшается на единицу, а не обрушивается.

    Иначе человек, купивший восемь устройств, после одного нажатия получал три.
    """
    service, vpn = billing
    await user_factory(**{
        'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 10,
        'vpn.extraDevices': [{'id': 'p1', 'amount': 2, 'active': True,
                              'pricePerDevice': 75, 'nextChargeAt': None}]})

    assert await service.remove(1, 1) == 9


async def test_limit_never_goes_below_the_free_one(db, user_factory, billing):
    service, vpn = billing
    await user_factory(**{'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 3})

    assert await service.remove(1, 10) == 2      # бесплатных 2
