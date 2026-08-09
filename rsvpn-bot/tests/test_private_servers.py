"""Личные серверы: покупка, выдача, участники, продление."""

from datetime import timedelta

import pytest

from app.core.time import now
from app.domain import private_servers as ps
from app.repositories.private_servers import PrivateServersRepository
from app.repositories.users import UsersRepository
from app.services.private_servers import PrivateServerService
from app.settings.service import SettingsService

SQUAD = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


class FakeVpn:
    """Панель: помнит, кому какие сквады и срок выставили."""

    def __init__(self):
        self.state: dict[str, dict] = {}
        self.fail = False

    async def create_subscription(self, user_id: int, days: int) -> dict:
        uuid = f'u-{user_id}'
        self.state[uuid] = {'squads': [], 'expireAt': now() + timedelta(days=days),
                            'usedTrafficBytes': 0}
        return {'uuid': uuid, 'shortUuid': f's-{user_id}',
                'expireAt': self.state[uuid]['expireAt'], 'createdAt': now()}

    async def update_subscription(self, uuid, *, expire_at=None, squads=None,
                                  status=None, **kw):
        if self.fail:
            raise RuntimeError('panel down')
        row = self.state.setdefault(uuid, {'squads': [], 'expireAt': None,
                                           'usedTrafficBytes': 0})
        if squads is not None:
            row['squads'] = list(squads)
        if expire_at is not None:
            row['expireAt'] = expire_at
        if status is not None:
            row['status'] = status
        return {'uuid': uuid}

    async def get_subscription(self, uuid: str) -> dict:
        row = self.state.get(uuid) or {}
        return {'usedTrafficBytes': row.get('usedTrafficBytes', 0),
                'onlineAt': None, 'status': row.get('status', 'ACTIVE')}


@pytest.fixture
async def service(db):
    users = UsersRepository(db['users'])
    settings = SettingsService(db['bot_settings'])
    vpn = FakeVpn()
    servers = PrivateServersRepository(db['private_servers'])
    return PrivateServerService(users, servers, settings, vpn), vpn, users


async def owner_with_money(users, user_id=1, balance=3000):
    await users.create({'user_data': {'user_id': user_id}, 'info': {'balance': balance},
                        'vpn': {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                                'expireAt': now() + timedelta(days=5),
                                'activeInternalSquads': ['общий']}})


async def live_server(service_tuple, owner=1, plan='company', location='ams'):
    service, vpn, users = service_tuple
    await owner_with_money(users, owner)
    result = await service.request(owner, plan, location=location, profile='grpc')
    await service.activate(result.server['_id'], SQUAD)
    return await service.servers.get(result.server['_id'])


# ── покупка ─────────────────────────────────────────────────────────────────
async def test_buying_charges_and_creates_a_request(service, db):
    srv, _, users = service
    await owner_with_money(users)

    result = await srv.request(1, 'company', location='ams')

    assert result.ok and result.amount == 1500
    assert result.server['status'] == ps.REQUESTED
    assert (await users.get(1))['info']['balance'] == 1500


async def test_no_money_no_request(service, db):
    srv, _, users = service
    await owner_with_money(users, balance=100)

    result = await srv.request(1, 'company', location='ams')

    assert not result.ok and result.reason == 'no_funds'
    assert await db['private_servers'].count_documents({}) == 0


async def test_second_server_is_refused(service):
    srv, _, users = service
    await owner_with_money(users)
    await srv.request(1, 'mini', location='ams')

    result = await srv.request(1, 'mini', location='ams')

    assert not result.ok and result.reason == 'already_has'
    assert (await users.get(1))['info']['balance'] == 3000 - 990, 'деньги списаны дважды'


async def test_price_comes_from_settings(service):
    srv, _, users = service
    await srv.settings.set('private.price_mini', 1200)
    await owner_with_money(users)

    result = await srv.request(1, 'mini', location='ams')

    assert result.amount == 1200


async def test_rejected_request_returns_the_money(service):
    srv, _, users = service
    await owner_with_money(users)
    result = await srv.request(1, 'company', location='ams')

    await srv.reject(result.server['_id'])

    assert (await users.get(1))['info']['balance'] == 3000
    assert (await srv.servers.get(result.server['_id']))['status'] == ps.CANCELLED


# ── выдача ──────────────────────────────────────────────────────────────────
async def test_activation_gives_the_owner_the_squad(service):
    srv, vpn, users = service
    server = await live_server(service)

    assert server['status'] == ps.ACTIVE
    assert SQUAD in vpn.state['u-1']['squads']
    assert 'общий' in vpn.state['u-1']['squads'], 'свои сервера отбирать нельзя'


# ── участники ───────────────────────────────────────────────────────────────
async def test_invite_and_join(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() - timedelta(days=10),
                                'activeInternalSquads': []}})

    invite = await srv.invite(server['_id'], 1)
    result = await srv.join(invite.reason, 2)

    assert result.ok
    assert SQUAD in vpn.state['u-2']['squads']
    assert ps.occupied(result.server) == 2


async def test_membership_grants_time_even_without_a_subscription(service):
    """Смысл тарифа в том, что друзьям не нужна своя подписка."""
    srv, vpn, users = service
    server = await live_server(service)
    expired = now() - timedelta(days=10)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': expired, 'activeInternalSquads': []}})

    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    assert vpn.state['u-2']['expireAt'] > now()


async def test_leaving_returns_the_persons_own_expiry(service):
    """Иначе выход с сервера дарил бы месяц обычной подписки."""
    srv, vpn, users = service
    server = await live_server(service)
    expired = now() - timedelta(days=10)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': expired, 'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    await srv.leave(server['_id'], 2)

    assert SQUAD not in vpn.state['u-2']['squads']
    assert vpn.state['u-2']['expireAt'] == expired


async def test_invite_works_once(service):
    srv, _, users = service
    server = await live_server(service)
    for user_id in (2, 3):
        await users.create({'user_data': {'user_id': user_id}, 'info': {'balance': 0},
                            'vpn': {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                                    'expireAt': now() + timedelta(days=1),
                                    'activeInternalSquads': []}})

    invite = await srv.invite(server['_id'], 1)
    assert (await srv.join(invite.reason, 2)).ok
    second = await srv.join(invite.reason, 3)

    assert not second.ok and second.reason == 'bad_code'


async def test_slots_are_not_oversold(service):
    srv, _, users = service
    server = await live_server(service, plan='mini')      # 5 мест, владелец занял одно
    joined = 0
    for user_id in range(2, 9):
        await users.create({'user_data': {'user_id': user_id}, 'info': {'balance': 0},
                            'vpn': {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                                    'expireAt': now() + timedelta(days=1),
                                    'activeInternalSquads': []}})
        invite = await srv.invite(server['_id'], 1)
        if invite.ok and (await srv.join(invite.reason, user_id)).ok:
            joined += 1

    assert joined == 4, 'в пятиместный сервер влезло больше пяти'


async def test_owner_cannot_join_his_own_server(service):
    srv, _, _ = service
    server = await live_server(service)

    invite = await srv.invite(server['_id'], 1)
    result = await srv.join(invite.reason, 1)

    assert not result.ok and result.reason == 'own_server'


async def test_stranger_cannot_invite(service):
    srv, _, _ = service
    server = await live_server(service)

    result = await srv.invite(server['_id'], 999)

    assert not result.ok and result.reason == 'not_owner'


async def test_kicked_member_loses_access(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() + timedelta(days=1),
                                'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    result = await srv.kick(server['_id'], 1, 2)

    assert result.ok
    assert SQUAD not in vpn.state['u-2']['squads']
    assert ps.free_slots(result.server) == 9


async def test_panel_failure_frees_the_slot_back(service):
    """Иначе место числится занятым человеком, у которого нет доступа."""
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() + timedelta(days=1),
                                'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    vpn.fail = True

    result = await srv.join(invite.reason, 2)

    assert not result.ok and result.reason == 'panel'
    assert ps.occupied(await srv.servers.get(server['_id'])) == 1


# ── продление ───────────────────────────────────────────────────────────────
async def test_monthly_charge_extends_everyone(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() + timedelta(days=1),
                                'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)
    await srv.servers.set(server['_id'], next_charge_at=now() - timedelta(minutes=1))

    report = await srv.charge_due()

    assert report['charged'] == 1 and report['amount'] == 1500
    fresh = await srv.servers.get(server['_id'])
    assert fresh['paid_until'] > now() + timedelta(days=29)
    assert vpn.state['u-2']['expireAt'] > now() + timedelta(days=29)


async def test_no_money_suspends_and_cuts_access(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.col.update_one({'user_data.user_id': 1},
                               {'$set': {'info.balance': 10}})
    await srv.servers.set(server['_id'], next_charge_at=now() - timedelta(minutes=1))

    report = await srv.charge_due()

    assert report['suspended'] == 1
    assert (await srv.servers.get(server['_id']))['status'] == ps.SUSPENDED
    assert SQUAD not in vpn.state['u-1']['squads']


async def test_suspended_too_long_is_closed(service):
    srv, _, users = service
    server = await live_server(service)
    await srv.servers.set(server['_id'], status=ps.SUSPENDED,
                          suspended_at=now() - timedelta(days=ps.GRACE_DAYS + 1))

    report = await srv.charge_due()

    assert report['closed'] == 1
    assert (await srv.servers.get(server['_id']))['status'] == ps.CANCELLED


# ── статистика ──────────────────────────────────────────────────────────────
async def test_stats_lists_everyone_with_traffic(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2, 'first_name': 'Друг'},
                        'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() + timedelta(days=1),
                                'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)
    vpn.state['u-2']['usedTrafficBytes'] = 3 * 1024 ** 3

    rows = await srv.stats(await srv.servers.get(server['_id']))

    assert len(rows) == 2
    assert rows[0]['owner'] is True
    assert ps.gb(rows[1]['traffic']) == 3.0


async def test_stats_survive_a_dead_panel(service):
    """Экран статистики не должен падать оттого, что панель прилегла."""
    srv, vpn, users = service
    server = await live_server(service)

    async def boom(uuid):
        raise RuntimeError('panel down')

    vpn.get_subscription = boom
    rows = await srv.stats(server)

    assert len(rows) == 1 and rows[0]['traffic'] == 0


# ── тарифная арифметика ─────────────────────────────────────────────────────
def test_guests_exclude_the_owner():
    assert ps.BY_CODE['mini'].guests == 4
    assert ps.BY_CODE['team'].guests == 14


def test_free_slots_counts_the_owner():
    server = {'plan': 'mini', 'slots': 5, 'members': [{'user_id': 2}, {'user_id': 3}]}
    assert ps.occupied(server) == 3
    assert ps.free_slots(server) == 2


# ── ежемесячная цена ────────────────────────────────────────────────────────
#
# Первый месяц оплачивается при заказе, дальше столько же каждые 30 дней.
# Списание без предупреждения и без возможности отказаться — это «бот украл
# полторы тысячи» в поддержке, даже когда всё по договорённости.

async def test_first_month_is_paid_at_order_and_the_next_one_in_a_month(service):
    srv, _, users = service
    server = await live_server(service)

    assert (await users.get(1))['info']['balance'] == 3000 - 1500
    assert server['paid_until'] > now() + timedelta(days=29)
    assert server['next_charge_at'] == server['paid_until']


async def test_owner_is_warned_before_the_monthly_charge(service):
    srv, _, users = service
    sent = []

    async def remember(user_id, text):
        sent.append((user_id, text))

    srv._tell = remember
    server = await live_server(service)
    await srv.servers.set(server['_id'],
                          next_charge_at=now() + timedelta(days=ps.WARN_DAYS - 1))

    report = await srv.charge_due()

    assert report['warned'] == 1
    assert '1500₽' in sent[0][1]


async def test_the_warning_is_sent_once_per_cycle(service):
    srv, _, users = service
    async def silent(user_id, text):
        return None

    srv._tell = silent
    server = await live_server(service)
    await srv.servers.set(server['_id'],
                          next_charge_at=now() + timedelta(days=ps.WARN_DAYS - 1))

    assert (await srv.charge_due())['warned'] == 1
    assert (await srv.charge_due())['warned'] == 0, 'предупредили дважды за один цикл'


async def test_a_new_cycle_warns_again(service):
    srv, _, users = service
    async def silent(user_id, text):
        return None

    srv._tell = silent
    server = await live_server(service)
    await srv.servers.set(server['_id'], charge_warned_at=now(),
                          next_charge_at=now() - timedelta(minutes=1))

    await srv.charge_due()      # списали месяц — отметка сбрасывается
    fresh = await srv.servers.get(server['_id'])
    assert fresh['charge_warned_at'] is None


async def test_owner_can_refuse_the_next_charge(service):
    """Оплаченный месяц остаётся за человеком, следующий не списывается."""
    srv, _, users = service
    server = await live_server(service)

    result = await srv.set_autorenew(server['_id'], 1, False)

    assert result.ok and result.server['autorenew'] is False
    assert (await users.get(1))['info']['balance'] == 1500, 'деньги не возвращаются'


async def test_refused_server_closes_instead_of_charging(service):
    srv, vpn, users = service
    server = await live_server(service)
    await srv.set_autorenew(server['_id'], 1, False)
    await srv.servers.set(server['_id'], next_charge_at=now() - timedelta(minutes=1))

    report = await srv.charge_due()

    assert report['closed'] == 1 and report['charged'] == 0
    assert (await users.get(1))['info']['balance'] == 1500, 'списали, хотя отказались'
    assert SQUAD not in vpn.state['u-1']['squads']


async def test_refusal_can_be_taken_back(service):
    srv, _, users = service
    server = await live_server(service)
    await srv.set_autorenew(server['_id'], 1, False)

    await srv.set_autorenew(server['_id'], 1, True)
    await srv.servers.set(server['_id'], next_charge_at=now() - timedelta(minutes=1))
    report = await srv.charge_due()

    assert report['charged'] == 1


async def test_a_stranger_cannot_switch_off_someone_elses_renewal(service):
    srv, _, _ = service
    server = await live_server(service)

    result = await srv.set_autorenew(server['_id'], 999, False)

    assert not result.ok and result.reason == 'not_owner'
    assert (await srv.servers.get(server['_id']))['autorenew'] is True


# ── локация и протокол ──────────────────────────────────────────────────────
#
# От них зависит скорость и лимит трафика, поэтому выбирает их покупатель,
# а не админ при выдаче: переиграть молча — значит продать не то.

async def test_choice_is_saved_with_the_request(service):
    srv, _, users = service
    await owner_with_money(users)

    result = await srv.request(1, 'mini', location='tyo', profile='hysteria2')

    assert result.server['location'] == 'tyo'
    assert result.server['profile'] == 'hysteria2'
    assert result.server['traffic_gb'] == 0, 'Токио безлимитный'


async def test_limited_location_keeps_its_quota(service):
    srv, _, users = service
    await owner_with_money(users)

    result = await srv.request(1, 'mini', location='hkg', profile='reality')

    assert result.server['traffic_gb'] == 1024


async def test_location_is_required(service):
    srv, _, users = service
    await owner_with_money(users)

    result = await srv.request(1, 'mini', location='марс')

    assert not result.ok and result.reason == 'unknown_location'
    assert (await users.get(1))['info']['balance'] == 3000, 'списали за несуществующее'


async def test_unknown_profile_falls_back_to_the_recommended_one(service):
    """Протокол — не то, из-за чего стоит ронять покупку."""
    srv, _, users = service
    await owner_with_money(users)

    result = await srv.request(1, 'mini', location='ams', profile='чтототам')

    assert result.ok and result.server['profile'] == ps.DEFAULT_PROFILE


async def test_activation_does_not_overwrite_the_chosen_location(service):
    srv, _, users = service
    await owner_with_money(users)
    result = await srv.request(1, 'mini', location='mil', profile='grpc')

    await srv.activate(result.server['_id'], SQUAD)

    server = await srv.servers.get(result.server['_id'])
    assert server['location'] == 'mil' and server['profile'] == 'grpc'


def test_every_location_and_profile_has_a_short_unique_code():
    """Код едет в callback_data вместе с тарифом и протоколом: 64 байта на всё."""
    codes = [loc.code for loc in ps.LOCATIONS]
    assert len(codes) == len(set(codes)), codes
    assert all(len(code) <= 5 for code in codes), codes

    profiles = [p.code for p in ps.PROFILES]
    assert len(profiles) == len(set(profiles))
    longest = max(len(f'srv:prof:{p}:{c}:{pr}')
                  for p in ps.BY_CODE for c in codes for pr in profiles)
    assert longest <= 64, longest


def test_frankfurt_exists_in_both_flavours():
    """Одна площадка с лимитом, другая без — это разные серверы, не опечатка."""
    frankfurts = [loc for loc in ps.LOCATIONS if loc.title == 'Франкфурт']
    assert len(frankfurts) == 2
    assert {loc.limited for loc in frankfurts} == {True, False}


def test_traffic_titles_read_like_a_human_wrote_them():
    assert ps.BY_LOCATION['ams'].traffic_title == '1 ТБ'
    assert ps.BY_LOCATION['nl'].traffic_title == 'безлимит'


# ── трафик на экране ────────────────────────────────────────────────────────
#
# 4.91 МБ в гигабайтах — это 0.0 после округления. На новом сервере первые
# дни это единственные цифры, которые вообще есть, и экран показывал нули.

def test_small_traffic_is_not_rounded_into_nothing():
    assert ps.traffic(4.91 * 1024 ** 2) == '4.91 МБ'
    assert ps.traffic(5024) == '4.91 КБ'
    assert ps.traffic(900) == '900 Б'
    assert ps.traffic(0) == '0 Б'


def test_big_traffic_reads_in_the_right_unit():
    assert ps.traffic(3 * 1024 ** 3) == '3 ГБ'
    assert ps.traffic(2 * 1024 ** 4) == '2 ТБ'


def test_traffic_survives_garbage_from_the_panel():
    assert ps.traffic(None) == '0 Б'
    assert ps.traffic('нет данных') == '0 Б'


async def test_stats_read_traffic_under_any_field_name(service):
    """Имена полей у панели менялись между версиями."""
    srv, vpn, users = service
    server = await live_server(service)

    async def old_panel(uuid):
        return {'lifetimeUsedTrafficBytes': 5 * 1024 ** 2,
                'lastConnectedAt': None, 'status': 'ACTIVE'}

    vpn.get_subscription = old_panel
    rows = await srv.stats(server)

    assert ps.traffic(rows[0]['traffic']) == '5 МБ'


async def test_a_panel_failure_is_marked_not_silently_zeroed(service):
    """Нули и молчание панели должны различаться на экране."""
    srv, vpn, users = service
    server = await live_server(service)

    async def boom(uuid):
        raise RuntimeError('panel down')

    vpn.get_subscription = boom
    rows = await srv.stats(server)

    assert rows[0]['error'] and rows[0]['traffic'] == 0


async def test_missing_panel_subscription_is_named_not_shown_as_zero(service):
    """Три причины нулей неразличимы на экране, если их не назвать."""
    srv, vpn, users = service
    server = await live_server(service)
    await users.set_vpn(1, {'uuid': ''})

    rows = await srv.stats(server)

    assert rows[0]['error'] == 'нет подписки в панели'


async def test_panel_without_traffic_fields_is_reported(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def empty(uuid):
        return {'status': 'ACTIVE', 'username': '802421217'}

    vpn.get_subscription = empty
    rows = await srv.stats(server)

    assert rows[0]['error'] == 'панель не отдала трафик'
    assert 'username' in rows[0]['fields'], 'без полей ответа чинить нечего'


async def test_zero_traffic_on_a_working_panel_is_not_an_error(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def fresh(uuid):
        return {'usedTrafficBytes': 0, 'status': 'ACTIVE'}

    vpn.get_subscription = fresh
    rows = await srv.stats(server)

    assert not rows[0]['error'] and rows[0]['traffic'] == 0


async def test_stats_find_traffic_in_a_nested_response(service):
    """Разные версии панели заворачивают пользователя на разную глубину."""
    srv, vpn, users = service
    server = await live_server(service)

    async def nested(uuid):
        return {'response': {'user': {'uuid': uuid,
                                      'usedTrafficBytes': 7 * 1024 ** 2}}}

    vpn.get_subscription = nested
    rows = await srv.stats(server)

    assert ps.traffic(rows[0]['traffic']) == '7 МБ'
    assert not rows[0]['error']


async def test_stats_find_traffic_in_a_list_response(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def as_list(uuid):
        return [{'uuid': uuid, 'usedTrafficBytes': 2 * 1024 ** 3}]

    vpn.get_subscription = as_list
    rows = await srv.stats(server)

    assert ps.traffic(rows[0]['traffic']) == '2 ГБ'


# ── уведомления об участниках ───────────────────────────────────────────────
#
# Владелец раздал ссылку и ушёл: без сообщения он узнаёт о новом участнике,
# только если сам зайдёт и пересчитает места.

class Telling:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, user_id, text, **kw):
        self.sent.append((user_id, text))


async def _with_friend(service, bot):
    srv, vpn, users = service
    srv.bot = bot
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2, 'first_name': 'Даша'},
                        'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'expireAt': now() + timedelta(days=1),
                                'activeInternalSquads': []}})
    return server


async def test_owner_learns_that_someone_joined(service):
    srv, _, users = service
    bot = Telling()
    server = await _with_friend(service, bot)

    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    assert bot.sent, 'владелец не узнал о новом участнике'
    who, text = bot.sent[-1]
    assert who == 1
    assert 'Даша' in text and '2 из 10' in text


async def test_owner_learns_that_someone_left(service):
    srv, _, users = service
    bot = Telling()
    server = await _with_friend(service, bot)
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)
    bot.sent.clear()

    await srv.leave(server['_id'], 2)

    who, text = bot.sent[-1]
    assert who == 1 and 'Даша' in text


async def test_kicked_member_is_told_why_the_vpn_stopped(service):
    """Иначе человек решит, что сломался VPN, и пойдёт в поддержку."""
    srv, _, users = service
    bot = Telling()
    server = await _with_friend(service, bot)
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)
    bot.sent.clear()

    await srv.kick(server['_id'], 1, 2)

    who, text = bot.sent[-1]
    assert who == 2 and 'закрыл вам доступ' in text
