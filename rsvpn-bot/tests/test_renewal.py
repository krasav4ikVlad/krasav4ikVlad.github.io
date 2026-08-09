"""Автопродление: порядок операций, атомарность, просроченные подписки."""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.services.renewal import RenewalService
from app.services.topup import TopupService
from app.settings.service import SettingsService


class FakeVpn:
    def __init__(self, fail_for: set[str] | None = None):
        self.calls: list[dict] = []
        self.fail_for = fail_for or set()

    async def update_subscription(self, uuid, *, expire_at=None, **kw):
        if uuid in self.fail_for:
            raise VpnPanelError('panel down')
        self.calls.append({'uuid': uuid, 'expire_at': expire_at})
        return {'uuid': uuid}


@pytest.fixture
async def renewal(db):
    settings = SettingsService(db['bot_settings'])
    users = UsersRepository(db['users'])
    plans = PlansRepository(db['plans'])
    await plans.seed()
    topup = TopupService(users, PaymentsRepository(db['payments']), settings)
    vpn = FakeVpn()
    return RenewalService(users, plans, settings, vpn, topup), vpn


async def make_subscriber(user_factory, hours_left=2, balance=200, period=30, **extra):
    return await user_factory(**{
        'info.balance': balance,
        'vpn.shortUuid': 's-1', 'vpn.uuid': 'u-1', 'vpn.period': period,
        'vpn.hwidDeviceLimit': 2,
        'vpn.expireAt': now() + timedelta(hours=hours_left),
        **extra,
    })


async def test_renews_and_charges(db, user_factory, renewal):
    service, vpn = renewal
    await make_subscriber(user_factory, hours_left=2, balance=200)

    report = await service.run()

    assert report.renewed == 1
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 50          # 200 − 150 за месяц
    assert vpn.calls[0]['uuid'] == 'u-1'


async def test_money_is_taken_before_the_panel(db, user_factory, renewal):
    """Порядок важен: если панель упадёт, деньги вернутся; наоборот — нет."""
    service, vpn = renewal
    vpn.fail_for = {'u-1'}
    await make_subscriber(user_factory, balance=200)

    report = await service.run()

    assert report.failed == 1
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 200         # списание откатано
    transactions = [t['description'] for t in user['info']['transactions']]
    assert any('Возврат' in t for t in transactions)


async def test_not_enough_money_is_not_an_error(db, user_factory, renewal):
    service, vpn = renewal
    await make_subscriber(user_factory, balance=10)

    report = await service.run()

    assert report.no_funds == 1 and vpn.calls == []
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 10


async def test_expired_subscription_is_still_renewed(db, user_factory, renewal):
    """Бот полежал сутки — подписка не должна умирать при живом балансе."""
    service, vpn = renewal
    await make_subscriber(user_factory, hours_left=-10, balance=200)

    report = await service.run()
    assert report.renewed == 1


async def test_long_dead_subscription_is_left_alone(db, user_factory, renewal):
    service, vpn = renewal
    await make_subscriber(user_factory, hours_left=-200, balance=200)

    report = await service.run()
    assert report.renewed == 0 and report.notes.get('too_old') == 1


async def test_renewal_from_now_when_already_expired(db, user_factory, renewal):
    """Продление просроченной считается от сегодня, а не «в прошлое»."""
    service, vpn = renewal
    await make_subscriber(user_factory, hours_left=-10, balance=200)

    await service.run()
    assert vpn.calls[0]['expire_at'] > now() + timedelta(days=29)


async def test_far_future_subscriptions_are_not_touched(db, user_factory, renewal):
    service, vpn = renewal
    await make_subscriber(user_factory, hours_left=100, balance=200)

    report = await service.run()
    assert report.checked == 0 and vpn.calls == []


async def test_bypass_is_extended_only_when_it_exists(db, user_factory, renewal):
    service, vpn = renewal
    await make_subscriber(user_factory, balance=200)
    await make_subscriber(user_factory, balance=200, **{'vpn.bypass_uuid': 'bp-1'})

    await service.run()

    assert [c['uuid'] for c in vpn.calls] == ['u-1', 'u-1', 'bp-1']


async def test_devices_are_not_charged_twice(db, user_factory, renewal):
    """Продление берёт только цену тарифа.

    За доп. устройства платят их пакеты в DeviceBillingService, у которых свой
    тридцатидневный цикл. Складывать одно с другим значит списывать за
    устройства дважды в месяц, а на дневном тарифе — каждый день.
    """
    service, vpn = renewal
    await make_subscriber(user_factory, balance=400, **{'vpn.hwidDeviceLimit': 4})

    await service.run()

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 250          # 400 − 150, без 2×75


async def test_autorenew_can_be_switched_off(db, user_factory, renewal):
    service, vpn = renewal
    await service.settings.set('features.autorenew_enabled', False)
    await make_subscriber(user_factory, balance=200)

    report = await service.run()
    assert report.checked == 0 and vpn.calls == []


async def test_reminder_flags_reset_after_renewal(db, user_factory, renewal):
    service, vpn = renewal
    users = service.users

    class Expiry:
        def __init__(self):
            self.reset_for = []

        async def reset_after_renewal(self, user_id):
            self.reset_for.append(user_id)

    service.expiry = Expiry()
    await make_subscriber(user_factory, balance=200, **{'vpn.notified': {'1d': True}})

    await service.run()
    assert service.expiry.reset_for == [1]


# ── срок без тарифа ─────────────────────────────────────────────────────────
#
# В vpn.period попадала длина бесплатного периода — 3 дня. Тарифа на 3 дня
# нет, by_days(3) возвращал None, автопродление отвечало 'unknown_plan' и
# молча ничего не делало. Живой баланс, ни одной ошибки в логе, подписка
# умирает — и так у каждого, кто пришёл через триал.

async def test_renewal_heals_a_period_that_has_no_plan(db, user_factory, renewal):
    service, _ = renewal
    await make_subscriber(user_factory, period=3, balance=100)

    report = await service.run()

    assert report.renewed == 1, report.notes
    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['vpn']['period'] == 1, 'срок не починен — на следующем проходе то же самое'
    assert doc['info']['balance'] == 94


async def test_renewal_charges_full_price_when_the_promo_is_off(db, user_factory, renewal):
    """Акция «вернись со скидкой» — приглашение вернуться, а не новый прайс."""
    from app.services.discounts import DiscountService

    service, _ = renewal
    service.discounts = DiscountService(service.settings)
    await service.settings.set('discount.no_active', 0.5)
    await service.settings.set('discount.on_autorenew', False)
    await make_subscriber(user_factory, period=1, balance=100,
                          **{'growth.segment': 'trial'})

    await service.run()

    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['info']['balance'] == 94


async def test_renewal_keeps_the_promo_when_it_is_on(db, user_factory, renewal):
    """Включено — списание совпадает с ценой, которую человек видит на экране."""
    from app.services.discounts import DiscountService

    service, _ = renewal
    service.discounts = DiscountService(service.settings)
    await service.settings.set('discount.no_active', 0.5)
    await make_subscriber(user_factory, period=1, balance=100,
                          **{'growth.segment': 'trial'})

    await service.run()

    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['info']['balance'] == 97
