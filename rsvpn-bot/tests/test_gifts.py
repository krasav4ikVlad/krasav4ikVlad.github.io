"""Подарки: одна активация ссылки, атомарное списание у дарителя."""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.services.gifts import GiftService
from app.settings.service import SettingsService


class FakeVpn:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = []
        self.updated = []

    async def create_subscription(self, user_id, days):
        if self.fail:
            raise VpnPanelError('panel down')
        self.created.append({'user_id': user_id, 'days': days})
        return {'uuid': 'new-uuid', 'shortUuid': 'new-short',
                'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    async def update_subscription(self, uuid, *, expire_at=None, **kw):
        if self.fail:
            raise VpnPanelError('panel down')
        self.updated.append({'uuid': uuid, 'expire_at': expire_at})
        return {}


@pytest.fixture
async def gifts(db):
    plans = PlansRepository(db['plans'])
    await plans.seed()
    vpn = FakeVpn()
    service = GiftService(UsersRepository(db['users']), db['gifts'], plans,
                          SettingsService(db['bot_settings']), vpn)
    await service.ensure_indexes()
    return service, vpn


async def test_gift_creates_subscription_for_new_user(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 500})     # даритель
    await user_factory()                            # получатель
    gift_id = await service.create(1, '1month')

    result = await service.activate(gift_id, '1month', from_user_id=1, to_user_id=2)

    assert result.ok and result.days == 30 and result.extended is False
    sender = await db['users'].find_one({'user_data.user_id': 1})
    assert sender['info']['balance'] == 350         # 500 − 150
    assert vpn.created[0]['user_id'] == 2


async def test_gift_extends_existing_subscription(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 500})
    await user_factory(**{'vpn.uuid': 'u-2', 'vpn.expireAt': now() + timedelta(days=5)})
    gift_id = await service.create(1, '1month')

    result = await service.activate(gift_id, '1month', 1, 2)

    assert result.ok and result.extended is True
    assert vpn.updated[0]['expire_at'] > now() + timedelta(days=34)


async def test_link_works_only_once(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 500})
    await user_factory()
    gift_id = await service.create(1, '1month')

    first = await service.activate(gift_id, '1month', 1, 2)
    second = await service.activate(gift_id, '1month', 1, 2)

    assert first.ok and not second.ok and second.reason == 'used'
    sender = await db['users'].find_one({'user_data.user_id': 1})
    assert sender['info']['balance'] == 350          # списано один раз


async def test_free_gift_is_used_instead_of_money(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 500, 'info.gifts.1month': 1})
    await user_factory()
    gift_id = await service.create(1, '1month')

    result = await service.activate(gift_id, '1month', 1, 2)

    assert result.ok and result.price == 0
    sender = await db['users'].find_one({'user_data.user_id': 1})
    assert sender['info']['balance'] == 500
    assert sender['info']['gifts']['1month'] == 0


async def test_free_gift_cannot_go_negative(db, user_factory, gifts):
    """Раньше счётчик читался, уменьшался в питоне и записывался целиком."""
    service, vpn = gifts
    await user_factory(**{'info.balance': 500, 'info.gifts.1month': 1})
    await user_factory()
    await user_factory()

    first = await service.activate(await service.create(1, '1month'), '1month', 1, 2)
    second = await service.activate(await service.create(1, '1month'), '1month', 1, 3)

    assert first.price == 0 and second.price == 150   # второй уже за деньги
    sender = await db['users'].find_one({'user_data.user_id': 1})
    assert sender['info']['gifts']['1month'] == 0


async def test_sender_without_money_gets_refusal(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 10})
    await user_factory()
    gift_id = await service.create(1, '1month')

    result = await service.activate(gift_id, '1month', 1, 2)

    assert not result.ok and result.reason == 'no_funds'
    gift = await db['gifts'].find_one({'gift_id': gift_id})
    assert gift['is_accepted'] is False              # ссылка снова рабочая


async def test_cannot_accept_own_gift(db, user_factory, gifts):
    service, vpn = gifts
    await user_factory(**{'info.balance': 500})
    gift_id = await service.create(1, '1month')

    assert (await service.activate(gift_id, '1month', 1, 1)).reason == 'self'


async def test_panel_failure_returns_money_and_link(db, user_factory, gifts):
    service, vpn = gifts
    vpn.fail = True
    await user_factory(**{'info.balance': 500})
    await user_factory()
    gift_id = await service.create(1, '1month')

    result = await service.activate(gift_id, '1month', 1, 2)

    assert not result.ok and result.reason == 'panel_error'
    sender = await db['users'].find_one({'user_data.user_id': 1})
    assert sender['info']['balance'] == 500
    gift = await db['gifts'].find_one({'gift_id': gift_id})
    assert gift['is_accepted'] is False


async def test_legacy_gift_code_still_works(db, user_factory, gifts):
    """Ссылки с кодом 3years были выданы раньше — они должны открываться."""
    service, vpn = gifts
    await user_factory(**{'info.balance': 5000})
    await user_factory()
    gift_id = await service.create(1, '3years')

    result = await service.activate(gift_id, '3years', 1, 2)

    assert result.ok and result.days == 1095


# ── витрина остатков ────────────────────────────────────────────────────────
def test_spent_gifts_are_not_shown_as_available():
    """Ключ остаётся в документе после траты подарка — но «0 шт.» на экране
    читается как доступный подарок, которого нет."""
    from app.bot.screens.profile import gifts_block

    block = gifts_block({'1day': 22, '1month': 0},
                        {'1day': 'Ежедневная', '1month': '1 месяц'})

    assert block == 'Ежедневная: 22 шт.'


def test_all_zero_gifts_read_as_empty():
    from app.bot.screens.profile import gifts_block

    assert gifts_block({'1month': 0, '3month': 0}, {}) == 'Пока нет'
    assert gifts_block({}, {}) == 'Пока нет'


def test_broken_counter_does_not_break_the_screen():
    from app.bot.screens.profile import gifts_block

    assert gifts_block({'1day': None, '1month': 'три', '3month': 2},
                       {'3month': '3 месяца'}) == '3 месяца: 2 шт.'
