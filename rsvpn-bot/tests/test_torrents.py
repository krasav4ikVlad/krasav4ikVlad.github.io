"""Торренты: предупреждение человеку, повторному — отключение подписки.

Событие присылает плагин Torrent Blocker панели (вебхук
`torrent_blocker.report`). Для нас оно важнее, чем для него: из-за
торрентов блокируют сервер целиком, и без доступа остаются все.

Три правила, ради которых всё это написано:

  * человек должен получить ОДНО сообщение на одну блокировку — плагин за
    сессию присылает пачку отчётов;
  * «навсегда» должно быть навсегда: новая покупка не должна возвращать
    доступ (панель на новой подписке включает запись обратно сама);
  * бот остаётся открытым — иначе человек не узнает, за что, и не сможет
    написать в поддержку.
"""

from datetime import timedelta

import pytest

from app.core.errors import VpnLocked
from app.core.time import now
from app.repositories.users import UsersRepository
from app.services.moderation import ModerationService
from app.services.torrents import TorrentGuard
from app.settings.service import SettingsService

USER = 802421217


@pytest.fixture
def users(db):
    return UsersRepository(db['users'])


@pytest.fixture
def settings(db):
    return SettingsService(db['bot_settings'])


class FakeVpn:
    """Панель: запоминает, кому что переключили, и умеет выдать подписку.

    Выдача нужна, чтобы проверка «отключённый не может купить» падала
    по делу: без неё покупка сорвалась бы на отсутствии метода, и тест
    зеленел бы даже со снятой защитой.
    """

    def __init__(self):
        self.statuses: list[tuple[str, str]] = []

    async def set_status(self, ref, status):
        self.statuses.append((str(ref), status))
        return {}

    async def create_subscription(self, user_id, days):
        return {'uuid': f'uuid-{user_id}', 'shortUuid': f'short-{user_id}',
                'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    async def renew_subscription(self, user, expire_at):
        return {'expireAt': expire_at}, []

    async def update_subscription(self, ref, **kwargs):
        return {}


class FakeSender:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send(self, bot, user_id, text, markup=None):
        self.sent.append((user_id, text))
        return True


class FakeNotifier:
    def __init__(self):
        self.said: list[dict] = []

    async def torrent(self, user_id, count, node='', ip='', blocked=False):
        self.said.append({'user_id': user_id, 'count': count, 'node': node,
                          'ip': ip, 'blocked': blocked})
        return True


@pytest.fixture
def vpn():
    return FakeVpn()


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def guard(users, settings, sender, vpn, notifier):
    moderation = ModerationService(users, settings, vpn=vpn)
    return TorrentGuard(users, settings, sender, bot=None,
                        moderation=moderation, notifier=notifier)


async def client(users, user_id: int = USER) -> dict:
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'first_name': 'Иван'},
        'info': {'balance': 1000}, 'growth': {},
        'vpn': {'uuid': f'uuid-{user_id}', 'shortUuid': f'short-{user_id}',
                'period': 30}})
    return await users.get(user_id)


def report(username=str(USER), *, ip='1.2.3.4', seconds=3600,
           node='Netherlands-1', uuid=f'uuid-{USER}',
           short=f'short-{USER}') -> dict:
    """Что присылает панель. Форма — из контракта @remnawave/backend-contract."""
    return {
        'node': {'uuid': 'node-1', 'name': node, 'countryCode': 'NL'},
        'user': {'uuid': uuid, 'username': username,
                 'telegramId': None, 'shortUuid': short},
        'report': {
            'actionReport': {'blocked': True, 'ip': ip,
                             'blockDuration': seconds,
                             'willUnblockAt': (now() + timedelta(
                                 seconds=seconds)).isoformat(),
                             'userId': '1', 'processedAt': now().isoformat()},
            'xrayReport': {'email': f'{USER}', 'level': 0,
                           'protocol': 'bittorrent', 'network': 'udp',
                           'source': ip, 'destination': '5.6.7.8:6881',
                           'routeTarget': None, 'originalTarget': None,
                           'inboundTag': 'VLESS', 'inboundName': None,
                           'inboundLocal': None, 'outboundTag': 'BLOCK',
                           'ts': 1}},
    }


# ── первое нарушение: разговор, а не наказание ──────────────────────────────
async def test_the_person_is_told_what_happened(guard, users, sender, db):
    await client(users)

    result = await guard.handle(report())

    assert result['note'] == 'warned_1'
    assert sender.sent and sender.sent[0][0] == USER
    assert 'торрент' in sender.sent[0][1].lower()


async def test_the_message_names_the_real_block_time(guard, users, sender, db):
    """В конфиге плагина это число меняется в один клик — зашитое в текст
    однажды станет враньём. Берём из отчёта: 3600 секунд = 60 минут."""
    await client(users)

    await guard.handle(report(seconds=3600))

    assert '60 мин' in sender.sent[0][1]


async def test_a_six_minute_block_is_told_as_six(guard, users, sender, db):
    await client(users)

    await guard.handle(report(seconds=360))

    assert '6 мин' in sender.sent[0][1]


async def test_the_first_offence_does_not_disable_anything(guard, users, vpn, db):
    await client(users)

    await guard.handle(report())

    assert vpn.statuses == []
    assert not ModerationService.vpn_locked(await users.get(USER))


async def test_the_bypass_username_is_the_same_person(guard, users, sender, db):
    """У запасной подписки имя в панели `<id>_bypass` — это он же."""
    await client(users)

    await guard.handle(report(username=f'{USER}_bypass'))

    assert sender.sent and sender.sent[0][0] == USER


async def test_a_stranger_is_not_invented(guard, users, sender, db):
    """Чужая подписка (другая панель, ручная запись) — не наш клиент."""
    await client(users)

    result = await guard.handle(report(username='999999', uuid='uuid-999999',
                                       short='short-999999'))

    assert result['note'] == 'user_not_found' and not sender.sent


# ── одна блокировка — одно сообщение ────────────────────────────────────────
#
# Торрент-клиент за сессию даёт десятки отчётов, и панель вдобавок ретраит
# вебхуки. Сорок одинаковых сообщений подряд — это не предупреждение.

async def test_a_burst_of_reports_is_one_offence(guard, users, sender, db):
    await client(users)

    for _ in range(5):
        await guard.handle(report())

    assert len(sender.sent) == 1
    assert users.pick(await users.get(USER), 'moderation.torrent.count') == 1


async def test_every_report_is_still_counted(guard, users, db):
    """Иначе не отличить «забыл выключить клиент» от «качает сутками»."""
    await client(users)

    for _ in range(5):
        await guard.handle(report())

    assert users.pick(await users.get(USER), 'moderation.torrent.reports') == 5


async def test_after_the_window_it_is_a_new_offence(guard, users, sender, db):
    await client(users)
    await guard.handle(report())
    await users.col.update_one(
        {'user_data.user_id': USER},
        {'$set': {'moderation.torrent.last_at': now() - timedelta(hours=5)}})

    await guard.handle(report())

    assert len(sender.sent) == 2


# ── повторное нарушение: подписка выключена ─────────────────────────────────
async def test_the_second_offence_disables_the_subscription(guard, users, vpn,
                                                            sender, db):
    await client(users)
    await guard.handle(report())
    await users.col.update_one(
        {'user_data.user_id': USER},
        {'$set': {'moderation.torrent.last_at': now() - timedelta(hours=5)}})

    result = await guard.handle(report())

    assert result['note'] == 'blocked_2'
    assert ('uuid-802421217', 'DISABLED') in vpn.statuses
    assert ModerationService.vpn_locked(await users.get(USER))
    assert 'отключена' in sender.sent[-1][1].lower()


async def test_the_threshold_is_a_setting(guard, users, settings, vpn, db):
    """Кому-то хватит одного раза."""
    await client(users)
    await settings.set('torrents.block_after', 1)

    result = await guard.handle(report())

    assert result['note'] == 'blocked_1' and vpn.statuses


async def test_zero_means_only_warnings(guard, users, settings, vpn, db):
    await client(users)
    await settings.set('torrents.block_after', 0)

    for _ in range(3):
        await users.col.update_one(
            {'user_data.user_id': USER},
            {'$set': {'moderation.torrent.last_at': now() - timedelta(hours=5)}})
        await guard.handle(report())

    assert vpn.statuses == []


async def test_the_bot_stays_open_after_the_block(guard, users, db):
    """Человек должен видеть, за что, и уметь написать в поддержку."""
    await client(users)
    await guard.handle(report())
    await users.col.update_one(
        {'user_data.user_id': USER},
        {'$set': {'moderation.torrent.last_at': now() - timedelta(hours=5)}})
    await guard.handle(report())

    assert not ModerationService.is_banned(await users.get(USER))


async def test_the_admins_are_told(guard, users, notifier, db):
    """Из-за торрентов банят сервер — знать об этом надо раньше хостера."""
    await client(users)

    await guard.handle(report())

    assert notifier.said and notifier.said[0]['node'] == 'Netherlands-1'


# ── «навсегда» должно быть навсегда ─────────────────────────────────────────
#
# Панель на новой подписке включает отключённую запись обратно — без
# проверки на нашей стороне блокировка снималась бы любой оплатой.

async def test_a_locked_person_cannot_buy(users, settings, db):
    from app.repositories.plans import PlansRepository
    from app.services.billing import BillingService

    await client(users)
    await ModerationService(users, settings, vpn=FakeVpn()).lock_vpn(USER, 'торренты')
    plans = PlansRepository(db['plans'])
    await plans.col.insert_one({'code': 'm1', 'title': 'Месяц', 'days': 30,
                                'price': 100, 'enabled': True})
    billing = BillingService(users, plans, settings, FakeVpn(), None)

    with pytest.raises(VpnLocked):
        await billing.buy(USER, 'm1')


async def test_a_locked_person_cannot_extend(users, settings, db):
    from app.repositories.plans import PlansRepository
    from app.services.billing import BillingService

    await client(users)
    await ModerationService(users, settings, vpn=FakeVpn()).lock_vpn(USER, 'торренты')
    billing = BillingService(users, PlansRepository(db['plans']), settings,
                             FakeVpn(), None)

    with pytest.raises(VpnLocked):
        await billing.extend(USER)


async def test_a_locked_person_gets_no_trial(users, settings, db):
    from app.services.trial import TrialService

    await client(users)
    await ModerationService(users, settings, vpn=FakeVpn()).lock_vpn(USER, 'торренты')
    trial = TrialService(users, settings, vpn=None)

    assert (await trial.claim(USER)).reason == 'locked'


async def test_the_money_stays_where_it_was(users, settings, db):
    """Отключение — не изъятие: баланс остаётся человеку."""
    await client(users)

    await ModerationService(users, settings, vpn=FakeVpn()).lock_vpn(USER, 'торренты')

    assert users.pick(await users.get(USER), 'info.balance') == 1000


# ── отмена ──────────────────────────────────────────────────────────────────
async def test_the_access_can_be_given_back(users, settings, vpn, db):
    await client(users)
    moderation = ModerationService(users, settings, vpn=vpn)
    await moderation.lock_vpn(USER, 'торренты')

    await moderation.unlock_vpn(USER, admin_id=1)

    assert not ModerationService.vpn_locked(await users.get(USER))
    assert ('uuid-802421217', 'ACTIVE') in vpn.statuses


async def test_unlocking_does_not_resurrect_a_hard_ban(users, settings, vpn, db):
    """У забаненного жёстко замок другой, и снимать его должен /unban."""
    await client(users)
    moderation = ModerationService(users, settings, vpn=vpn)
    await moderation.ban(USER, admin_id=1, hard=True)
    vpn.statuses.clear()

    await moderation.unlock_vpn(USER, admin_id=1)

    assert ('uuid-802421217', 'ACTIVE') not in vpn.statuses


async def test_the_counter_is_reset_on_unlock(users, settings, vpn, db):
    """Иначе следующий торрент сразу отключит подписку снова."""
    await client(users)
    moderation = ModerationService(users, settings, vpn=vpn)
    await users.col.update_one({'user_data.user_id': USER},
                               {'$set': {'moderation.torrent.count': 2}})
    await moderation.lock_vpn(USER, 'торренты')

    await moderation.unlock_vpn(USER, admin_id=1)

    assert not users.pick(await users.get(USER), 'moderation.torrent.count')


# ── выключатель ─────────────────────────────────────────────────────────────
async def test_the_whole_thing_can_be_switched_off(guard, users, settings,
                                                   sender, db):
    await client(users)
    await settings.set('torrents.enabled', False)

    result = await guard.handle(report())

    assert result['note'] == 'disabled' and not sender.sent


async def test_the_person_can_be_left_unbothered(guard, users, settings,
                                                 sender, notifier, db):
    """Иногда нужно только смотреть — без писем клиентам."""
    await client(users)
    await settings.set('torrents.warn_user', False)

    await guard.handle(report())

    assert not sender.sent and notifier.said
