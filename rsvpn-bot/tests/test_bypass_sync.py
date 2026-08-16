"""ByPass кончается тогда же, когда основная подписка.

ByPass — отдельный пользователь в панели, и любое место, двигающее срок,
обязано двигать оба. Проверяем по одному тесту на каждое такое место:
пропуск виден человеку как «VPN отключился раньше времени», а в логах — никак.
"""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt
from app.repositories.users import UsersRepository
from app.services import bypass


class FakeVpn:
    def __init__(self, fail_for: set[str] | None = None):
        self.calls: list[dict] = []
        self.fail_for = fail_for or set()

    async def update_subscription(self, uuid, *, expire_at=None, **kw):
        if uuid in self.fail_for:
            raise VpnPanelError('panel down')
        self.calls.append({'uuid': uuid, 'expire_at': expire_at, **kw})
        return {'uuid': uuid}


@pytest.fixture
def users(db):
    return UsersRepository(db['users'])


async def subscriber(user_factory, main, theirs=None, uuid='bp-1',
                     devices=2, bypass_devices=2):
    return await user_factory(**{
        'vpn.uuid': 'u-1', 'vpn.shortUuid': 's-1', 'vpn.expireAt': main,
        'vpn.hwidDeviceLimit': devices,
        'vpn.bypass_uuid': uuid, 'vpn.bypass_expireAt': theirs,
        'vpn.bypass_hwidDeviceLimit': bypass_devices,
    })


# ── сам синхронизатор ───────────────────────────────────────────────────────
async def test_it_moves_bypass_to_the_main_date(db, users, user_factory):
    later = now() + timedelta(days=30)
    await subscriber(user_factory, main=later, theirs=now())
    vpn = FakeVpn()

    assert await bypass.sync_expiry(users, vpn, 1, later) == 'synced'
    assert vpn.calls == [{'uuid': 'bp-1', 'expire_at': later}]
    user = await users.get(1)
    assert parse_dt(user['vpn']['bypass_expireAt']) == later


async def test_without_bypass_nothing_happens(db, users, user_factory):
    await user_factory(**{'vpn.uuid': 'u-1', 'vpn.expireAt': now()})
    vpn = FakeVpn()

    assert await bypass.sync_expiry(users, vpn, 1, now()) == 'no_bypass'
    assert vpn.calls == []


async def test_matching_dates_do_not_touch_the_panel(db, users, user_factory):
    """Лишний запрос на каждого — это тысячи запросов на ровном месте."""
    same = now() + timedelta(days=10)
    await subscriber(user_factory, main=same, theirs=same + timedelta(seconds=5))
    vpn = FakeVpn()

    assert await bypass.sync_expiry(users, vpn, 1, same) == 'same'
    assert vpn.calls == []


async def test_a_refusing_panel_does_not_get_written_to_the_base(db, users, user_factory):
    """Дата в базе, которой нет в панели, — это «отключился раньше срока»."""
    later = now() + timedelta(days=30)
    await subscriber(user_factory, main=later, theirs=now())
    vpn = FakeVpn(fail_for={'bp-1'})

    assert await bypass.sync_expiry(users, vpn, 1, later) == 'panel_error'
    user = await users.get(1)
    assert parse_dt(user['vpn']['bypass_expireAt']) != later


# ── поиск и починка расхождений ─────────────────────────────────────────────
async def test_drift_is_found_and_fixed(db, users, user_factory):
    later = now() + timedelta(days=30)
    await subscriber(user_factory, main=later, theirs=now())          # разъехался
    await subscriber(user_factory, main=later, theirs=later, uuid='bp-2')   # в порядке
    vpn = FakeVpn()

    found = await bypass.find_drift(users)
    assert [row['user_id'] for row in found] == [1]

    report = await bypass.repair(users, vpn, apply=True)
    assert report == {'checked': 1, 'fixed': 1, 'failed': 0, 'rows': found}
    assert not await bypass.find_drift(users)


async def test_everyone_is_checked_not_just_the_first_page(db, users, user_factory):
    """Раньше сверка брала первую тысячу и отчитывалась «расхождений нет».

    Mongo отдаёт одну и ту же первую тысячу, поэтому все, кто дальше по
    коллекции, не проверялись никогда: у них ByPass отключался посреди
    оплаченного месяца, а в логах было чисто.
    """
    later = now() + timedelta(days=30)
    for number in range(1200):
        await subscriber(user_factory, main=later, theirs=now(),
                         uuid=f'bp-{number}')

    drift = await bypass.find_drift(users)

    assert len(drift) == 1200, f'проверено только {len(drift)}'


# ── лимит устройств ─────────────────────────────────────────────────────────
#
# У ByPass он свой. Пока его не двигали, человек покупал устройства и мог
# подключить их только к основной подписке — за то же самое.

async def test_buying_devices_raises_the_bypass_limit_too(db, users, user_factory):
    from app.services.devices import DeviceBillingService
    from app.settings.service import SettingsService

    vpn = FakeVpn()
    repo = UsersRepository(db['users'])
    service = DeviceBillingService(repo, SettingsService(db['bot_settings']), vpn)
    await subscriber(user_factory, main=now() + timedelta(days=30),
                     theirs=now() + timedelta(days=30))
    await repo.credit(1, 1000, 'тест')

    await service.add(1, 3)

    limits = {call['uuid']: call.get('device_limit') for call in vpn.calls}
    assert limits == {'u-1': 5, 'bp-1': 5}, vpn.calls
    user = await repo.get(1)
    assert user['vpn']['bypass_hwidDeviceLimit'] == 5


async def test_a_device_limit_mismatch_is_drift(db, users, user_factory):
    same = now() + timedelta(days=30)
    await subscriber(user_factory, main=same, theirs=same, devices=5,
                     bypass_devices=2)

    drift = await bypass.find_drift(users)

    assert len(drift) == 1 and drift[0]['device_limit'] is True
    assert drift[0]['dates'] is False, 'даты-то как раз совпадают'


async def test_repair_pushes_the_device_limit(db, users, user_factory):
    same = now() + timedelta(days=30)
    await subscriber(user_factory, main=same, theirs=same, devices=5,
                     bypass_devices=2)
    vpn = FakeVpn()

    await bypass.repair(users, vpn, apply=True)

    assert vpn.calls == [{'uuid': 'bp-1', 'expire_at': None, 'device_limit': 5}]
    assert not await bypass.find_drift(users)


async def test_an_unknown_bypass_limit_is_pushed_once(db, users, user_factory):
    """Пустое значение — это «мы не знаем, что в панели», а не «совпадает»."""
    same = now() + timedelta(days=30)
    await subscriber(user_factory, main=same, theirs=same, bypass_devices=None)
    vpn = FakeVpn()

    assert len(await bypass.find_drift(users)) == 1
    await bypass.repair(users, vpn, apply=True)
    assert not await bypass.find_drift(users), 'второй проход снова нашёл то же'


async def test_a_dry_run_changes_nothing(db, users, user_factory):
    await subscriber(user_factory, main=now() + timedelta(days=30), theirs=now())
    vpn = FakeVpn()

    report = await bypass.repair(users, vpn)

    assert report['checked'] == 1 and report['fixed'] == 0
    assert vpn.calls == []


async def test_a_missing_bypass_date_counts_as_drift(db, users, user_factory):
    """Пустая дата — это не «совпадает», это подписка неизвестно до какого дня."""
    await subscriber(user_factory, main=now() + timedelta(days=30), theirs=None)

    assert len(await bypass.find_drift(users)) == 1


# ── места, которые двигают срок ─────────────────────────────────────────────
async def test_renewal_keeps_them_together(db, user_factory):
    from app.repositories.payments import PaymentsRepository
    from app.repositories.plans import PlansRepository
    from app.services.renewal import RenewalService
    from app.services.topup import TopupService
    from app.settings.service import SettingsService

    settings = SettingsService(db['bot_settings'])
    repo = UsersRepository(db['users'])
    plans = PlansRepository(db['plans'])
    await plans.seed()
    vpn = FakeVpn()
    service = RenewalService(repo, plans, settings, vpn,
                             TopupService(repo, PaymentsRepository(db['payments']),
                                          settings))
    await user_factory(**{
        'info.balance': 500, 'vpn.uuid': 'u-1', 'vpn.shortUuid': 's-1',
        'vpn.period': 30, 'vpn.expireAt': now() + timedelta(hours=2),
        'vpn.bypass_uuid': 'bp-1', 'vpn.bypass_expireAt': now() + timedelta(hours=2)})

    await service.run()

    user = await repo.get(1)
    assert parse_dt(user['vpn']['expireAt']) == parse_dt(user['vpn']['bypass_expireAt'])


async def test_a_gift_keeps_them_together(db, user_factory):
    """Подарок двигал только основную — ByPass отключался посреди месяца."""
    from app.repositories.plans import PlansRepository
    from app.services.gifts import GiftService
    from app.settings.service import SettingsService

    repo = UsersRepository(db['users'])
    plans = PlansRepository(db['plans'])
    await plans.seed()
    vpn = FakeVpn()
    service = GiftService(repo, db['gifts'], plans, SettingsService(db['bot_settings']),
                          vpn)
    started = now() + timedelta(days=5)
    await user_factory(**{'vpn.uuid': 'u-1', 'vpn.shortUuid': 's-1',
                          'vpn.expireAt': started,
                          'vpn.bypass_uuid': 'bp-1', 'vpn.bypass_expireAt': started})

    await service._grant(1, {'code': '1month', 'days': 30, 'title': 'Месяц'})

    user = await repo.get(1)
    assert parse_dt(user['vpn']['expireAt']) == parse_dt(user['vpn']['bypass_expireAt'])
    assert {call['uuid'] for call in vpn.calls} == {'u-1', 'bp-1'}


async def test_a_private_server_keeps_them_together(db, user_factory):
    """Доступ к серверу продлевает срок до оплаченной владельцем даты."""
    from app.repositories.private_servers import PrivateServersRepository
    from app.services.private_servers import PrivateServerService
    from app.settings.service import SettingsService

    repo = UsersRepository(db['users'])
    servers = PrivateServersRepository(db['private_servers'])
    settings = SettingsService(db['bot_settings'])

    class Panel(FakeVpn):
        async def create_subscription(self, user_id, days):
            return {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                    'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    vpn = Panel()
    service = PrivateServerService(repo, servers, settings, vpn)
    soon = now() + timedelta(days=2)
    await user_factory(**{'info.balance': 3000, 'vpn.uuid': 'u-1',
                          'vpn.shortUuid': 's-1', 'vpn.expireAt': soon,
                          'vpn.bypass_uuid': 'bp-1', 'vpn.bypass_expireAt': soon})

    result = await service.request(1, 'mini', location='ams', profile='reality')
    await service.activate(result.server['_id'],
                           'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    user = await repo.get(1)
    assert parse_dt(user['vpn']['expireAt']) == parse_dt(user['vpn']['bypass_expireAt'])
    assert parse_dt(user['vpn']['expireAt']) > soon, 'срок вообще не продлился'
