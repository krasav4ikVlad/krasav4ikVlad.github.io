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
        self.markups: list = []

    async def send(self, bot, user_id, text, markup=None):
        self.sent.append((user_id, text))
        self.markups.append(markup)
        return True


class FakeNotifier:
    def __init__(self):
        self.said: list[dict] = []
        self.appeals: list[dict] = []

    async def torrent(self, user_id, count, node='', ip='', step='warn'):
        self.said.append({'user_id': user_id, 'count': count, 'node': node,
                          'ip': ip, 'step': step})
        return True

    async def torrent_appeal(self, user_id, stats, hint, code='', locked=False):
        self.appeals.append({'user_id': user_id, 'stats': stats, 'hint': hint,
                             'code': code, 'locked': locked})
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

    assert result['note'] == 'warn_1'
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
async def again(guard, users, times: int = 1) -> dict:
    """Ещё одно нарушение — в новом окне, а не в том же."""
    result = {}
    for _ in range(times):
        await users.col.update_one(
            {'user_data.user_id': USER},
            {'$set': {'moderation.torrent.last_at': now() - timedelta(hours=5)}})
        result = await guard.handle(report())
    return result


async def test_the_second_offence_freezes_the_subscription(guard, users, vpn,
                                                           sender, db):
    """Плагин уже закрыл IP на свои десять минут — это наша, своя ступень."""
    await client(users)
    await guard.handle(report())

    result = await again(guard, users)

    assert result['note'] == 'freeze_2'
    assert ('uuid-802421217', 'DISABLED') in vpn.statuses
    assert 'заморожена' in sender.sent[-1][1].lower()


async def test_the_freeze_has_a_deadline(guard, users, db):
    """Иначе «на полчаса» тихо станет «навсегда»."""
    await client(users)
    await guard.handle(report())
    await again(guard, users)

    card = await users.get(USER)
    assert users.pick(card, 'moderation.vpn_locked_until') is not None
    assert not ModerationService.locked_forever(card)


async def test_the_third_offence_disables_it_forever(guard, users, vpn,
                                                     sender, db):
    await client(users)
    await guard.handle(report())

    result = await again(guard, users, times=2)

    assert result['note'] == 'block_3'
    assert ModerationService.locked_forever(await users.get(USER))
    assert 'не возвращаются' in sender.sent[-1][1]


async def test_the_money_warning_comes_before_the_block(guard, users, sender, db):
    """Про невозврат человек должен прочитать заранее, а не после."""
    await client(users)

    await guard.handle(report())

    assert 'не возвращаются' in sender.sent[0][1]


async def test_the_steps_are_settings(guard, users, settings, vpn, db):
    """Кому-то хватит одного раза."""
    await client(users)
    await settings.set('torrents.freeze_at', 0)
    await settings.set('torrents.block_at', 1)

    result = await guard.handle(report())

    assert result['note'] == 'block_1' and vpn.statuses


async def test_zero_means_only_warnings(guard, users, settings, vpn, db):
    await client(users)
    await settings.set('torrents.freeze_at', 0)
    await settings.set('torrents.block_at', 0)

    await guard.handle(report())
    await again(guard, users, times=3)

    assert vpn.statuses == []


async def test_the_bot_stays_open_after_the_block(guard, users, db):
    """Человек должен видеть, за что, и уметь написать в поддержку."""
    await client(users)
    await guard.handle(report())
    await again(guard, users, times=2)

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


# ── заморозка кончается сама ────────────────────────────────────────────────
async def test_the_freeze_ends_by_itself(guard, users, vpn, sender, db):
    await client(users)
    await guard.handle(report())
    await again(guard, users)
    vpn.statuses.clear()
    await users.col.update_one(
        {'user_data.user_id': USER},
        {'$set': {'moderation.vpn_locked_until': now() - timedelta(minutes=1)}})

    thawed = await guard.thaw()

    assert thawed == 1
    assert ('uuid-802421217', 'ACTIVE') in vpn.statuses
    assert not ModerationService.vpn_locked(await users.get(USER))
    assert 'вернулся' in sender.sent[-1][1].lower()


async def test_an_unexpired_freeze_is_left_alone(guard, users, vpn, db):
    await client(users)
    await guard.handle(report())
    await again(guard, users)
    vpn.statuses.clear()

    assert await guard.thaw() == 0
    assert vpn.statuses == []


async def test_thawing_keeps_the_counter(guard, users, db):
    """Иначе лестница обнулялась бы каждые полчаса и до отключения дело не
    доходило бы никогда."""
    await client(users)
    await guard.handle(report())
    await again(guard, users)
    await users.col.update_one(
        {'user_data.user_id': USER},
        {'$set': {'moderation.vpn_locked_until': now() - timedelta(minutes=1)}})

    await guard.thaw()

    assert users.pick(await users.get(USER), 'moderation.torrent.count') == 2


async def test_a_permanent_block_is_never_thawed(guard, users, vpn, db):
    """У бессрочного отключения нет срока — и разморозка не должна его
    выдумывать."""
    await client(users)
    await guard.handle(report())
    await again(guard, users, times=2)
    vpn.statuses.clear()

    assert await guard.thaw() == 0
    assert ModerationService.locked_forever(await users.get(USER))


async def test_the_freeze_is_not_turned_into_a_permanent_one(guard, users, db):
    """Второй отчёт в ту же заморозку не должен отнимать у неё срок."""
    await client(users)
    await guard.handle(report())
    await again(guard, users)

    await guard.handle(report())      # в том же окне — не нарушение

    assert not ModerationService.locked_forever(await users.get(USER))


# ── «я не качаю торренты» ───────────────────────────────────────────────────
#
# Проверить это автоматически нечем: содержимого трафика у нас нет и быть
# не должно. Значит, решает человек — а наше дело дать ему то, по чему
# решают, и не наказывать того, кому поверили.

async def test_the_message_offers_a_way_to_object(guard, users, sender, db):
    await client(users)

    await guard.handle(report())

    assert sender.markups[0] is not None


async def test_the_button_can_be_switched_off(guard, users, settings, sender, db):
    await client(users)
    await settings.set('torrents.appeal', False)

    await guard.handle(report())

    assert sender.markups[0] is None


async def test_the_appeal_reaches_the_admins_with_the_evidence(guard, users,
                                                               notifier, db):
    await client(users)
    await guard.handle(report())

    assert await guard.appeal(USER) is True
    card = notifier.appeals[0]
    assert card['user_id'] == USER
    assert card['stats']['count'] == 1 and card['hint']


async def test_an_appeal_without_a_single_report_goes_nowhere(guard, users,
                                                              notifier, db):
    """Кнопку можно нажать из старого сообщения — жаловаться при этом не на что."""
    await client(users)

    assert await guard.appeal(USER) is False
    assert not notifier.appeals


async def test_believing_returns_the_access(guard, users, vpn, sender, db):
    await client(users)
    await guard.handle(report())
    await again(guard, users, times=2)

    await guard.decide(USER, trust=True, admin_id=1)

    assert not ModerationService.vpn_locked(await users.get(USER))
    assert ('uuid-802421217', 'ACTIVE') in vpn.statuses
    assert 'ложное срабатывание' in sender.sent[-1][1]


async def test_the_believed_are_not_punished_again(guard, users, vpn, sender, db):
    """Если у человека срабатывает ложно, оно сработает снова — второй раз
    объясняться ему незачем."""
    await client(users)
    await guard.decide(USER, trust=True, admin_id=1)
    sender.sent.clear()
    vpn.statuses.clear()

    for _ in range(3):
        await again(guard, users)

    assert vpn.statuses == [] and sender.sent == []


async def test_the_believed_are_still_visible_to_admins(guard, users, notifier, db):
    """Поверили зря — это должно быть видно, а не исчезнуть из ленты."""
    await client(users)
    await guard.decide(USER, trust=True, admin_id=1)
    notifier.said.clear()

    await again(guard, users)

    assert notifier.said


async def test_refusing_keeps_everything_as_it_was(guard, users, sender, db):
    await client(users)
    await guard.handle(report())
    await again(guard, users, times=2)

    await guard.decide(USER, trust=False, admin_id=1)

    assert ModerationService.locked_forever(await users.get(USER))
    assert 'раздача была' in sender.sent[-1][1]


# ── подсказка тому, кто разбирает ───────────────────────────────────────────
#
# Настоящий торрент отличается от случайного срабатывания упорством:
# десятки отчётов подряд и повторы в разные дни. Это и говорим.

def test_a_stream_of_reports_looks_real():
    from app.domain.torrents import REAL, verdict

    assert verdict({'reports': 40, 'count': 1})[0] == REAL


def test_a_single_report_looks_doubtful():
    from app.domain.torrents import DOUBTFUL, verdict

    assert verdict({'reports': 1, 'count': 1})[0] == DOUBTFUL


def test_two_nodes_in_one_story_look_real():
    from app.domain.torrents import REAL, verdict

    assert verdict({'reports': 2, 'count': 1,
                    'nodes': ['NL-1', 'DE-2']})[0] == REAL


def test_the_middle_is_called_unclear():
    """Худшее, что можно сделать, — выдать догадку за вывод."""
    from app.domain.torrents import UNCLEAR, verdict

    assert verdict({'reports': 5, 'count': 2})[0] == UNCLEAR


def test_the_ladder_is_read_from_the_settings():
    from app.domain.torrents import BLOCK, FREEZE, WARN, stage

    assert stage(1, freeze_at=2, block_at=3) == WARN
    assert stage(2, freeze_at=2, block_at=3) == FREEZE
    assert stage(3, freeze_at=2, block_at=3) == BLOCK


def test_a_mixed_up_ladder_errs_on_the_mild_side():
    """Пороги перепутали местами (отключать со второго, замораживать
    с пятого) — отключение всё равно не случится раньше заморозки."""
    from app.domain.torrents import BLOCK, WARN, stage

    assert stage(3, freeze_at=5, block_at=2) == WARN
    assert stage(5, freeze_at=5, block_at=2) == BLOCK


# ── чего человеку знать не надо ─────────────────────────────────────────────
#
# Настоящая причина запрета — что из-за торрентов блокируют сервер, и тогда
# без интернета остаются все. Написать это человеку значит выдать рычаг:
# обиженный узнает, что одним клиентом можно положить сервер целиком.
# Поэтому в текстах — правила и его собственные последствия, и ничего про
# то, чем ему вредно нам вредить.

LEVERS = ('банят', 'бан сервер', 'блокируют сервер', 'заблокируют сервер',
          'хостер', 'абуз', 'дата-центр', 'остаются все', 'положить')


def test_the_texts_do_not_hand_out_a_lever():
    from app.content.texts import REGISTRY

    for key, template in REGISTRY.items():
        if not key.startswith('torrent.'):
            continue
        body = template.default.lower()
        found = [word for word in LEVERS if word in body]
        assert not found, f'{key}: {found}'


def test_the_ban_is_explained_by_the_rules():
    """Причина всё-таки должна быть названа — иначе это выглядит произволом."""
    from app.content.texts import REGISTRY

    assert 'правил' in REGISTRY['torrent.warn'].default.lower()
