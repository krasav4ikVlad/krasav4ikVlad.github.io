"""Сверка лимита устройств: пакеты → база бота → панель.

Расходятся они молча: бот меняет лимит в панели и только потом пишет к
себе, и если панель в этот момент отказала, на экране одно число, а в
жизни другое. Проверяется, что сверка видит обе стороны расхождения и
чинит в правильном порядке.
"""

import pytest

from app.core.errors import VpnPanelError
from app.repositories.users import UsersRepository
from app.services import device_sync

BASE = 2


class Panel:
    """Панель, которая помнит, что у кого стоит, и что ей присылали."""

    def __init__(self, limits=None, broken=()):
        self.limits = dict(limits or {})
        self.broken = set(broken)
        self.patched: list[tuple] = []

    async def get_subscription(self, ref):
        if ref in self.broken:
            raise VpnPanelError('HTTP 500')
        if ref not in self.limits:
            return {}
        return {'hwidDeviceLimit': self.limits[ref]}

    async def update_subscription(self, ref, **kw):
        if ref in self.broken:
            raise VpnPanelError('HTTP 500')
        self.patched.append((ref, kw.get('device_limit')))
        self.limits[ref] = kw.get('device_limit')
        return {}


@pytest.fixture
def users(db):
    return UsersRepository(db['users'])


async def make(user_factory, user_id, stored, packages=(), ref=None):
    return await user_factory(**{
        'user_data.user_id': user_id,
        'vpn.uuid': ref or f'u-{user_id}',
        'vpn.hwidDeviceLimit': stored,
        'vpn.extraDevices': list(packages),
    })


def package(amount, active=True):
    return {'id': f'p{amount}', 'amount': amount, 'active': active}


# ── сколько оплачено ────────────────────────────────────────────────────────
def test_paid_limit_is_base_plus_active_packages():
    vpn = {'extraDevices': [package(2), package(1)]}

    assert device_sync.paid_limit(vpn, BASE) == 5


def test_a_switched_off_package_is_not_paid_for():
    """Пакет выключают, когда денег не хватило, — он больше не считается."""
    vpn = {'extraDevices': [package(2), package(3, active=False)]}

    assert device_sync.paid_limit(vpn, BASE) == 4


def test_without_packages_the_limit_is_the_free_one():
    assert device_sync.paid_limit({}, BASE) == 2


# ── база бота против оплаченного ────────────────────────────────────────────
async def test_a_bot_limit_below_what_was_paid_is_found(db, users, user_factory):
    """Человек заплатил за устройство, а панель в тот момент отказала."""
    await make(user_factory, 1, stored=2, packages=[package(3)])

    drift = await device_sync.find_drift(users, BASE)

    assert len(drift) == 1
    assert drift[0]['paid'] == 5 and drift[0]['stored'] == 2


async def test_a_bot_limit_above_what_was_paid_is_found_too(db, users, user_factory):
    """Лимит могли поднять руками — это тоже расхождение, а не подарок."""
    await make(user_factory, 1, stored=9)

    drift = await device_sync.find_drift(users, BASE)

    assert drift[0]['paid'] == 2 and drift[0]['stored'] == 9


async def test_people_who_agree_are_not_reported(db, users, user_factory):
    await make(user_factory, 1, stored=5, packages=[package(3)])
    await make(user_factory, 2, stored=2)

    assert await device_sync.find_drift(users, BASE) == []


async def test_people_without_a_subscription_are_skipped(db, users, user_factory):
    """Лимит без подписки ничего не значит — сравнивать не с чем."""
    await user_factory(**{'user_data.user_id': 1, 'vpn.uuid': '',
                          'vpn.hwidDeviceLimit': 7})

    assert await device_sync.find_drift(users, BASE) == []


# ── панель ──────────────────────────────────────────────────────────────────
async def test_a_silent_panel_mismatch_is_the_worst_case(db, users, user_factory):
    """Пакеты и бот согласны, а панель осталась со старым числом — на экране
    одно, в жизни другое, и внутренняя сверка этого не видит."""
    await make(user_factory, 1, stored=5, packages=[package(3)])
    panel = Panel({'u-1': 2})

    assert await device_sync.find_drift(users, BASE) == []
    report = await device_sync.check_panel(users, panel, BASE)

    assert len(report['drift']) == 1
    row = report['drift'][0]
    assert row['paid'] == 5 and row['panel'] == 2 and row['where'] == 'panel'


async def test_the_panel_check_says_nothing_when_all_three_agree(db, users,
                                                                 user_factory):
    await make(user_factory, 1, stored=5, packages=[package(3)])

    report = await device_sync.check_panel(users, Panel({'u-1': 5}), BASE)

    assert report['drift'] == [] and report['checked'] == 1


async def test_a_panel_that_does_not_answer_is_counted_not_hidden(db, users,
                                                                  user_factory):
    """Иначе «панель молчит» выглядело бы как «всё сходится»."""
    await make(user_factory, 1, stored=5, packages=[package(3)])

    report = await device_sync.check_panel(users, Panel(broken={'u-1'}), BASE)

    assert report['failed'] == 1 and report['drift'] == []


async def test_a_missing_limit_field_is_not_a_match(db, users, user_factory):
    await make(user_factory, 1, stored=5, packages=[package(3)])

    report = await device_sync.check_panel(users, Panel({}), BASE)

    assert report['failed'] == 1


async def test_the_check_stops_at_its_limit_and_says_so(db, users, user_factory):
    """Запрос на человека — без предела команда висла бы на всей базе."""
    for user_id in range(1, 6):
        await make(user_factory, user_id, stored=2)

    report = await device_sync.check_panel(users, Panel(), BASE, limit=3)

    assert report['checked'] == 3 and report['stopped'] is True


# ── починка ─────────────────────────────────────────────────────────────────
async def test_repair_brings_everything_to_what_was_paid(db, users, user_factory):
    await make(user_factory, 1, stored=2, packages=[package(3)])
    panel = Panel({'u-1': 2})
    drift = await device_sync.find_drift(users, BASE)

    report = await device_sync.repair(users, panel, drift, apply=True)

    assert report['fixed'] == 1
    assert panel.patched == [('u-1', 5)]
    assert (await users.get(1))['vpn']['hwidDeviceLimit'] == 5


async def test_showing_changes_nothing(db, users, user_factory):
    await make(user_factory, 1, stored=2, packages=[package(3)])
    panel = Panel({'u-1': 2})
    drift = await device_sync.find_drift(users, BASE)

    report = await device_sync.repair(users, panel, drift, apply=False)

    assert report['fixed'] == 0 and panel.patched == []
    assert (await users.get(1))['vpn']['hwidDeviceLimit'] == 2


async def test_a_refusing_panel_leaves_the_document_alone(db, users, user_factory):
    """Иначе бот показывал бы число, которого в панели нет, — ровно то
    расхождение, которое мы и чиним."""
    await make(user_factory, 1, stored=2, packages=[package(3)])
    drift = await device_sync.find_drift(users, BASE)

    report = await device_sync.repair(users, Panel(broken={'u-1'}), drift, apply=True)

    assert report['failed'] == 1 and report['fixed'] == 0
    assert (await users.get(1))['vpn']['hwidDeviceLimit'] == 2


async def test_bypass_gets_the_same_limit(db, users, user_factory):
    """У ByPass свой лимит: без этого расхождение переехало бы туда."""
    await user_factory(**{
        'user_data.user_id': 1, 'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 2,
        'vpn.extraDevices': [package(3)],
        'vpn.bypass_uuid': 'b-1', 'vpn.bypass_hwidDeviceLimit': 2,
    })
    panel = Panel({'u-1': 2, 'b-1': 2})

    await device_sync.repair(users, panel,
                             await device_sync.find_drift(users, BASE), apply=True)

    assert ('b-1', 5) in panel.patched


# ── карточка одного человека ────────────────────────────────────────────────
async def test_the_card_shows_all_three_numbers(db, users, user_factory):
    await make(user_factory, 1, stored=4, packages=[package(3)])

    card = await device_sync.one(users, Panel({'u-1': 2}), BASE, 1)

    assert card['paid'] == 5 and card['stored'] == 4 and card['panel'] == 2


async def test_the_card_admits_it_could_not_ask_the_panel(db, users, user_factory):
    await make(user_factory, 1, stored=2)

    card = await device_sync.one(users, Panel(broken={'u-1'}), BASE, 1)

    assert card['panel'] is None and 'HTTP 500' in card['panel_error']


async def test_the_card_of_a_person_without_a_subscription(db, users, user_factory):
    await user_factory(**{'user_data.user_id': 1, 'vpn.uuid': ''})

    card = await device_sync.one(users, Panel(), BASE, 1)

    assert card['panel_error'] == 'подписки в панели нет'


async def test_an_unknown_person_has_no_card(db, users):
    assert await device_sync.one(users, Panel(), BASE, 999) is None
