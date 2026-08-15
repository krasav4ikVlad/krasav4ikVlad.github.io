"""Личные серверы: покупка, выдача, участники, продление."""

from datetime import timedelta

import pytest

from app.core.time import now, parse_dt
from app.domain import private_servers as ps
from app.repositories.private_servers import PrivateServersRepository
from app.repositories.users import UsersRepository
from app.services.private_servers import PrivateServerService, is_banner_link
from app.settings.service import SettingsService

SQUAD = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


class FakeVpn:
    """Панель: помнит, кому какие сквады и срок выставили."""

    def __init__(self):
        self.state: dict[str, dict] = {}
        self.fail = False
        self.keys: list[str] = []

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
        # форма ответа как в спеке панели: трафик и онлайн внутри userTraffic
        return {'uuid': uuid, 'id': 1, 'status': row.get('status', 'ACTIVE'),
                'userTraffic': {'usedTrafficBytes': row.get('usedTrafficBytes', 0),
                                'onlineAt': None}}

    async def squad_usage(self, squad, start, end, **kw) -> dict:
        return {}

    async def squad_nodes(self, squad) -> list:
        return []

    async def node_users_usage(self, node_uuid, start, end, **kw) -> dict:
        return {}

    async def node_users_raw(self, node_uuid, start, end, top=200):
        return {}

    async def connection_keys(self, uuid, user_id=None) -> list:
        return list(self.keys)


@pytest.fixture
async def service(db):
    from app.repositories.server_pool import ServerPoolRepository

    users = UsersRepository(db['users'])
    settings = SettingsService(db['bot_settings'])
    vpn = FakeVpn()
    servers = PrivateServersRepository(db['private_servers'])
    pool = ServerPoolRepository(db['private_pool'])
    return PrivateServerService(users, servers, settings, vpn, pool=pool), vpn, users


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


# ── userTraffic ─────────────────────────────────────────────────────────────
#
# Так называет израсходованный трафик Remnawave 2.x. Ни одного из имён,
# которые перебирались раньше, в её ответе нет вовсе — отсюда и нули.

def test_traffic_is_read_from_user_traffic():
    from app.services.private_servers import _traffic_of

    assert _traffic_of({'userTraffic': 5 * 1024 ** 2}) == 5 * 1024 ** 2


def test_traffic_is_summed_from_any_shape():
    """userTraffic в разных сборках — число, объект с итогом или список нод."""
    from app.services.private_servers import _traffic_of

    assert _traffic_of({'userTraffic': {'total': 700}}) == 700
    assert _traffic_of({'userTraffic': [{'total': 100}, {'total': 50}]}) == 150
    assert _traffic_of({'userTraffic': {'node-a': 10, 'node-b': 20}}) == 30
    assert _traffic_of({'userTraffic': '4096'}) == 4096


def test_a_real_zero_is_not_a_missing_field():
    from app.services.private_servers import _traffic_of

    assert _traffic_of({'userTraffic': 0}) == 0
    assert _traffic_of({'status': 'ACTIVE'}) is None


async def test_stats_on_a_real_panel_answer(service):
    """Ответ той же формы, что пришёл с боевой панели."""
    srv, vpn, users = service
    server = await live_server(service)

    async def real(uuid):
        return {'uuid': uuid, 'id': 2549, 'username': '802421217',
                'status': 'ACTIVE', 'trafficLimitBytes': 0,
                'trafficLimitStrategy': 'NO_RESET', 'hwidDeviceLimit': 17,
                'userTraffic': 4.91 * 1024 ** 2, 'lastTrafficResetAt': None}

    vpn.get_subscription = real
    rows = await srv.stats(server)

    assert not rows[0]['error']
    assert ps.traffic(rows[0]['traffic']) == '4.91 МБ'


async def test_missing_online_field_is_not_reported_as_never_connected(service):
    """Панель этой версии времени подключения не отдаёт — врать нельзя."""
    srv, vpn, users = service
    server = await live_server(service)

    async def no_online(uuid):
        return {'uuid': uuid, 'userTraffic': 100, 'status': 'ACTIVE'}

    vpn.get_subscription = no_online
    rows = await srv.stats(server)

    assert rows[0]['online_known'] is False


# ── расход именно этого сервера ─────────────────────────────────────────────
#
# userTraffic в карточке — весь трафик человека по всем нодам, включая общие
# серверы RS VPN. Списывать его на личный сервер значит завышать расход и
# врать про квоту площадки. Сервер — это внутренний сквад, и у панели есть
# ручка ровно под него.

async def test_traffic_is_taken_from_the_server_squad(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def usage(squad, since, until, **kw):
        assert squad == SQUAD
        return {2549: 4 * 1024 ** 2}

    async def card(uuid):
        return {'uuid': uuid, 'id': 2549,
                'userTraffic': {'usedTrafficBytes': 900 * 1024 ** 3}}

    vpn.squad_usage, vpn.get_subscription = usage, card
    rows = await srv.stats(server)

    assert rows[0]['source'] == 'server'
    assert ps.traffic(rows[0]['traffic']) == '4 МБ', 'взяли общий трафик вместо серверного'


async def test_falls_back_to_the_user_card_when_the_squad_is_silent(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def broken(squad, since, until, **kw):
        raise RuntimeError('нет такой ручки')

    async def card(uuid):
        return {'uuid': uuid, 'id': 1,
                'userTraffic': {'usedTrafficBytes': 7 * 1024 ** 2}}

    vpn.squad_usage, vpn.get_subscription = broken, card
    rows = await srv.stats(server)

    assert rows[0]['source'] == 'user'
    assert ps.traffic(rows[0]['traffic']) == '7 МБ'
    assert rows[0]['usage_note']


async def test_last_connection_is_read_from_inside_user_traffic(service):
    """onlineAt лежит внутри userTraffic — сверху его искать бесполезно."""
    srv, vpn, users = service
    server = await live_server(service)
    moment = now() - timedelta(hours=2)

    async def card(uuid):
        return {'uuid': uuid, 'id': 1,
                'userTraffic': {'usedTrafficBytes': 10, 'onlineAt': moment.isoformat()}}

    vpn.get_subscription = card
    rows = await srv.stats(server)

    assert rows[0]['online_known'] is True
    assert rows[0]['online_at'] is not None


async def test_usage_is_requested_with_plain_dates(service):
    """В спеке параметры объявлены как format: date. С ISO-временем — отказ."""
    from app.integrations.vpn.remnawave import RemnawaveClient

    seen = {}

    class Http:
        async def request(self, method, url, headers=None, params=None, **kw):
            seen.update(params or {})

            class Reply:
                status_code = 200
                text = ''

                @staticmethod
                def json():
                    return {'response': {'squadUuid': SQUAD, 'hasMore': False,
                                         'nextCursor': None,
                                         'users': [{'id': 7, 'totalBytes': 512}]}}

            return Reply()

    client = RemnawaveClient('https://panel', 'token', Http())
    usage = await client.squad_usage(SQUAD, now() - timedelta(days=30), now())

    assert usage == {7: 512}
    assert len(seen['start']) == 10 and len(seen['end']) == 10, seen


async def test_fallback_reason_is_recorded_for_diagnostics(service):
    """Иначе «показан весь трафик» опять превращается в загадку."""
    srv, vpn, users = service
    server = await live_server(service)

    async def broken(squad):
        raise RuntimeError('HTTP 500 всё плохо')

    vpn.squad_nodes = broken
    rows = await srv.stats(server)

    assert 'HTTP 500' in rows[0]['usage_note']


async def test_usage_period_covers_a_full_month(service):
    """Нода могла работать и до привязки к боту — её расход тоже наш."""
    srv, vpn, users = service
    server = await live_server(service)
    window = {}

    async def usage(squad, since, until, **kw):
        window['since'], window['until'] = since, until
        return {}

    vpn.squad_usage = usage
    await srv.stats(await srv.servers.get(server['_id']))

    assert (window['until'] - window['since']).days >= ps.CHARGE_PERIOD_DAYS
    assert window['since'] < parse_dt(server['activated_at']), \
        'окно с даты активации на свежем сервере схлопывается в один день'


async def test_older_panel_is_counted_by_nodes(service):
    """Ручки расхода по скваду в старых версиях нет — считаем по нодам."""
    srv, vpn, users = service
    server = await live_server(service)

    async def no_squad_endpoint(squad, since, until, **kw):
        return {}                      # клиент проглотил 404 и вернул пусто

    async def nodes(squad):
        return [{'uuid': '94c3792c-5915-41d0-929e-00b949a28635'}]

    async def by_node(node_uuid, since, until, **kw):
        return {'1': 6 * 1024 ** 2}    # username = telegram id строкой

    async def card(uuid):
        return {'uuid': uuid, 'id': 2549, 'username': '1',
                'userTraffic': {'usedTrafficBytes': 2 * 1024 ** 4}}

    vpn.squad_usage, vpn.squad_nodes = no_squad_endpoint, nodes
    vpn.node_users_usage, vpn.get_subscription = by_node, card
    rows = await srv.stats(server)

    assert rows[0]['source'] == 'server'
    assert ps.traffic(rows[0]['traffic']) == '6 МБ', 'снова взяли общий трафик'


async def test_missing_squad_endpoint_is_asked_only_once():
    """404 на каждый показ статистики — лишний запрос на пустом месте."""
    from app.integrations.vpn.remnawave import RemnawaveClient

    calls = []

    class Http:
        async def request(self, method, url, headers=None, params=None, **kw):
            calls.append(url)

            class Reply:
                status_code = 404
                text = '{"message":"Cannot GET"}'

                @staticmethod
                def json():
                    return {}

            return Reply()

    client = RemnawaveClient('https://panel', 'token', Http())
    assert await client.squad_usage(SQUAD, now(), now()) == {}
    assert await client.squad_usage(SQUAD, now(), now()) == {}

    assert len(calls) == 1, calls


async def test_usage_window_is_not_a_single_day(service):
    """«Сегодня — сегодня» на свежем сервере даёт пустой диапазон."""
    srv, vpn, users = service
    server = await live_server(service)
    window = {}

    async def usage(squad, since, until, **kw):
        window['since'], window['until'] = since, until
        return {}

    vpn.squad_usage = usage
    await srv.stats(await srv.servers.get(server['_id']))

    assert window['until'].date() > window['since'].date()


async def test_node_usage_is_read_from_any_row_shape():
    """Форма ответа у версий панели разная — совпасть можно любым ключом."""
    from app.integrations.vpn.remnawave import usage_by_identifier

    # v3.2.2: categories/sparklineData/topUsers рядом, нужен только topUsers
    assert usage_by_identifier(
        {'categories': ['2026-08-10'], 'sparklineData': [1, 2],
         'topUsers': [{'color': '#fff', 'username': '802421217', 'total': 5}]}
    ) == {'802421217': 5}

    # плоский список записей
    assert usage_by_identifier([{'userUuid': 'x', 'totalBytes': 7}]) == {'x': 7}

    # числовой id раскладывается и строкой, и числом
    assert usage_by_identifier({'users': [{'id': 2549, 'total': 9}]}) == {'2549': 9, 2549: 9}


async def test_empty_node_usage_names_which_case_it_is(service):
    """«Пусто» бывает от отсутствия нод и от пустого периода — это разное."""
    srv, vpn, users = service
    server = await live_server(service)

    async def nodes_without_uuid(squad):
        return [{'name': 'нода без uuid'}]

    vpn.squad_nodes = nodes_without_uuid
    rows = await srv.stats(server)
    assert 'без опознаваемого uuid' in rows[0]['usage_note']


async def test_usage_probe_shows_what_the_panel_actually_returned(service):
    """Пересказ ответа своими словами трижды оказывался неточным."""
    srv, vpn, users = service
    server = await live_server(service)

    async def nodes(squad):
        return [{'uuid': 'node-1', 'name': 'Свой сервер'}]

    async def raw(node_uuid, since, until, top=200):
        return {'topUsers': [], 'categories': []}

    vpn.squad_nodes, vpn.node_users_raw = nodes, raw
    probe = await srv.usage_probe(server)

    assert any('нод в скваде: 1' in line for line in probe)
    assert any('topUsers' in line for line in probe)
    assert any('период:' in line for line in probe)


async def test_an_honest_zero_is_not_replaced_by_the_whole_traffic(service):
    """Панель ответила «ноль» — это ответ, а не отсутствие данных.

    Подставлять вместо него общий трафик человека нельзя: на свежем сервере
    2 ТБ вместо нуля пугают сильнее, чем честный ноль.
    """
    srv, vpn, users = service
    server = await live_server(service)

    async def nodes(squad):
        return [{'uuid': '94c3792c-5915-41d0-929e-00b949a28635'}]

    async def empty(node_uuid, since, until, **kw):
        return {}                       # topUsers пуст, но запрос удался

    async def card(uuid):
        return {'uuid': uuid, 'id': 2549, 'username': '1',
                'userTraffic': {'usedTrafficBytes': 2 * 1024 ** 4}}

    vpn.squad_nodes, vpn.node_users_usage = nodes, empty
    vpn.get_subscription = card
    rows = await srv.stats(server)

    assert rows[0]['source'] == 'server'
    assert rows[0]['traffic'] == 0
    assert not rows[0].get('usage_note')


async def test_a_silent_panel_still_falls_back(service):
    """А вот если панель не ответила — общий трафик лучше, чем ничего."""
    srv, vpn, users = service
    server = await live_server(service)

    async def broken(squad):
        raise RuntimeError('HTTP 500')

    async def card(uuid):
        return {'uuid': uuid, 'id': 1, 'userTraffic': {'usedTrafficBytes': 4096}}

    vpn.squad_nodes, vpn.get_subscription = broken, card
    rows = await srv.stats(server)

    assert rows[0]['source'] == 'user' and rows[0]['traffic'] == 4096


# ── долевой сервер ──────────────────────────────────────────────────────────
#
# Одна машина на трёх владельцев. Для каждого это его сервер: свои места,
# свои приглашения, своя статистика. Соседей по машине он видеть не должен
# нигде — ни в участниках, ни в цифрах, ни в приглашениях.

async def share_server(service_tuple, owner, squad=SQUAD):
    service, vpn, users = service_tuple
    await owner_with_money(users, owner)
    # Долю продаём только на безлимитных площадках — «ams» она бы не приняла.
    result = await service.request(owner, 'share', location='nl', profile='reality')
    await service.activate(result.server['_id'], squad)
    return await service.servers.get(result.server['_id'])


async def test_three_shares_fit_on_one_machine(service):
    srv, vpn, users = service
    first = await share_server(service, 1)
    second = await share_server(service, 2)
    third = await share_server(service, 3)

    assert all(s['status'] == ps.ACTIVE for s in (first, second, third))
    assert len({s['_id'] for s in (first, second, third)}) == 3, 'это один документ'
    assert len(await srv.servers.on_squad(SQUAD)) == 3


async def test_a_share_cannot_be_bought_on_a_limited_location(service):
    """Терабайт на трёх покупателей с их людьми кончится за месяц."""
    srv, vpn, users = service
    await owner_with_money(users, 1)

    result = await srv.request(1, 'share', location='ams', profile='reality')

    assert not result.ok and result.reason == 'limited_location'
    assert (await users.get(1))['info']['balance'] == 3000, 'деньги списаны'


async def test_only_unlimited_locations_are_offered_for_a_share(service):
    assert ps.locations_for(ps.BY_CODE['share']) == ps.UNLIMITED
    assert ps.locations_for(ps.BY_CODE['mini']) == ps.LOCATIONS
    assert not ps.allowed_location(ps.BY_CODE['share'], ps.BY_LOCATION['ams'])
    assert ps.allowed_location(ps.BY_CODE['share'], ps.BY_LOCATION['tyo'])


async def test_the_fourth_share_is_refused(service):
    """Иначе канал делится на четверых, а продано было на троих."""
    srv, vpn, users = service
    for owner in (1, 2, 3):
        await share_server(service, owner)
    await owner_with_money(users, 4)
    extra = await srv.request(4, 'share', location='nl', profile='reality')

    result = await srv.activate(extra.server['_id'], SQUAD)

    assert not result.ok and result.reason == 'squad_full'
    assert (await srv.servers.get(extra.server['_id']))['status'] == ps.REQUESTED


async def test_a_whole_server_does_not_share_a_machine(service):
    """Обычный тариф занимает машину целиком: тот же сквад — это опечатка."""
    srv, vpn, users = service
    await live_server(service, owner=1)
    await owner_with_money(users, 2)
    second = await srv.request(2, 'company', location='ams', profile='grpc')

    result = await srv.activate(second.server['_id'], SQUAD)

    assert not result.ok and result.reason == 'squad_busy'


async def test_shares_of_different_plans_do_not_mix(service):
    srv, vpn, users = service
    await share_server(service, 1)
    await owner_with_money(users, 2)
    other = await srv.request(2, 'mini', location='nl', profile='reality')

    result = await srv.activate(other.server['_id'], SQUAD)

    assert not result.ok and result.reason in ('squad_busy', 'squad_mismatch')


async def test_neighbours_are_invisible_in_stats(service):
    """Главное свойство тарифа: сосед по машине не должен быть виден."""
    srv, vpn, users = service
    mine = await share_server(service, 1)
    await share_server(service, 2)
    await users.create({'user_data': {'user_id': 20}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-20'}})
    invite = await srv.invite(mine['_id'], 1)
    await srv.join(invite.reason, 20)

    rows = await srv.stats(await srv.servers.get(mine['_id']))

    assert {row['user_id'] for row in rows} == {1, 20}, 'в статистике чужие люди'


async def test_a_neighbours_invite_does_not_open_my_share(service):
    srv, vpn, users = service
    mine = await share_server(service, 1)
    neighbour = await share_server(service, 2)
    await users.create({'user_data': {'user_id': 20}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-20'}})

    invite = await srv.invite(neighbour['_id'], 2)
    await srv.join(invite.reason, 20)

    assert not ps.is_member(await srv.servers.get(mine['_id']), 20)
    assert len((await srv.servers.get(neighbour['_id']))['members']) == 1


async def test_leaving_one_share_keeps_access_to_the_other(service):
    """Сквад на машине общий: снять его — значит выгнать человека оттуда,
    откуда его никто не выгонял."""
    srv, vpn, users = service
    first = await share_server(service, 1)
    second = await share_server(service, 2)
    await users.create({'user_data': {'user_id': 20}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-20', 'shortUuid': 's-20',
                                'activeInternalSquads': []}})
    for server, owner in ((first, 1), (second, 2)):
        invite = await srv.invite(server['_id'], owner)
        await srv.join(invite.reason, 20)

    await srv.leave(first['_id'], 20)

    assert SQUAD in vpn.state['u-20']['squads'], 'потерял доступ ко второй доле'
    assert not ps.is_member(await srv.servers.get(first['_id']), 20)


async def test_leaving_the_last_share_does_revoke_access(service):
    srv, vpn, users = service
    first = await share_server(service, 1)
    await users.create({'user_data': {'user_id': 20}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-20', 'shortUuid': 's-20',
                                'activeInternalSquads': []}})
    invite = await srv.invite(first['_id'], 1)
    await srv.join(invite.reason, 20)

    await srv.leave(first['_id'], 20)

    assert SQUAD not in vpn.state['u-20']['squads']


async def test_closing_a_share_does_not_tell_admins_to_kill_the_machine(service, db):
    """«Можно гасить VPS» под последней долей выключило бы сервер соседям."""
    srv, vpn, users = service
    mine = await share_server(service, 1)
    await share_server(service, 2)

    sent = []

    class Notifier:
        async def send(self, topic, text, **kw):
            sent.append(text)

    srv.notifier = Notifier()
    await srv.close(mine, reason='тест')

    assert sent and 'не гасить' in sent[-1], sent


async def test_closing_the_last_share_frees_the_machine(service):
    srv, vpn, users = service
    only = await share_server(service, 1)

    sent = []

    class Notifier:
        async def send(self, topic, text, **kw):
            sent.append(text)

    srv.notifier = Notifier()
    await srv.close(only, reason='тест')

    assert sent and 'можно гасить' in sent[-1], sent


async def test_wiping_a_server_frees_the_owner_to_buy_again(service, db):
    """Команда для тестов: закрытый сервер виден в истории, а этот — нет."""
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2',
                                'activeInternalSquads': []}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    result = await srv.wipe(server['_id'])

    assert result.ok and result.amount == 0, 'деньги вернулись без спроса'
    assert await db['private_servers'].count_documents({}) == 0
    assert not await srv.servers.of_owner(1), 'бот не даст купить второй'
    assert SQUAD not in vpn.state['u-2']['squads'], 'участник остался с доступом'


async def test_wiping_can_return_the_money(service):
    srv, vpn, users = service
    server = await live_server(service)
    before = (await users.get(1))['info']['balance']

    result = await srv.wipe(server['_id'], refund=True)

    assert result.amount == server['price']
    assert (await users.get(1))['info']['balance'] == before + server['price']


async def test_wiping_a_share_leaves_the_neighbours_alone(service):
    srv, vpn, users = service
    mine = await share_server(service, 1)
    neighbour = await share_server(service, 2)

    await srv.wipe(mine['_id'])

    assert await srv.servers.get(neighbour['_id']), 'снесли соседнюю долю'
    assert SQUAD in vpn.state['u-2']['squads'], 'сосед потерял доступ'


async def test_free_squads_show_where_a_share_fits(service):
    srv, vpn, users = service
    await share_server(service, 1)

    free = await srv.free_squads('nl', 'reality')

    assert free == [{'squad': SQUAD, 'used': 1, 'limit': 3, 'title': 'Нидерланды',
                     'location': 'nl', 'profile': 'reality'}]
    assert await srv.free_squads('tyo', 'reality') == [], 'чужая локация'
    assert await srv.free_squads('nl', 'grpc') == [], 'другой протокол'


async def test_a_full_machine_is_not_offered(service):
    srv, vpn, users = service
    for owner in (1, 2, 3):
        await share_server(service, owner)

    assert await srv.free_squads('nl', 'reality') == []


async def test_the_share_price_is_a_third(service):
    srv, vpn, users = service
    await owner_with_money(users, 1)

    result = await srv.request(1, 'share', location='nl', profile='reality')

    assert result.amount == 390
    assert (await users.get(1))['info']['balance'] == 3000 - 390
    assert result.server['slots'] == 5, 'владелец и четверо друзей'


async def test_the_number_of_shares_comes_from_settings(service):
    srv, vpn, users = service
    await srv.settings.set('private.shares', 2)
    await share_server(service, 1)
    await share_server(service, 2)
    await owner_with_money(users, 3)
    third = await srv.request(3, 'share', location='nl', profile='reality')

    result = await srv.activate(third.server['_id'], SQUAD)

    assert not result.ok and result.reason == 'squad_full'


# ── запас машин и очередь заявок ────────────────────────────────────────────
#
# Провижининг ручной, и это узкое место: заявка ждёт человека. Машины,
# поднятые заранее, снимают ожидание — заявка садится на готовый сквад сразу.

SPARE = 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'


async def test_a_spare_machine_is_handed_out_at_once(service):
    srv, vpn, users = service
    await owner_with_money(users, 1)
    await srv.pool_add(SPARE, 'ams', 'reality')
    result = await srv.request(1, 'mini', location='ams', profile='reality')

    given = await srv.give_from_pool(result.server['_id'])

    assert given.ok
    server = await srv.servers.get(result.server['_id'])
    assert server['status'] == ps.ACTIVE and server['squad_uuid'] == SPARE
    assert SPARE in vpn.state['u-1']['squads'], 'доступ владельцу не выдан'


async def test_a_machine_of_another_location_does_not_fit(service):
    srv, vpn, users = service
    await owner_with_money(users, 1)
    await srv.pool_add(SPARE, 'tyo', 'reality')
    result = await srv.request(1, 'mini', location='ams', profile='reality')

    given = await srv.give_from_pool(result.server['_id'])

    assert not given.ok and given.reason == 'no_free_machine'


async def test_a_protocol_mismatch_does_not_fit_either(service):
    """Протокол потом не поменять — выдать «почти то же» нельзя."""
    srv, vpn, users = service
    await owner_with_money(users, 1)
    await srv.pool_add(SPARE, 'ams', 'grpc')
    result = await srv.request(1, 'mini', location='ams', profile='reality')

    assert not (await srv.give_from_pool(result.server['_id'])).ok


async def test_a_taken_machine_is_not_offered_twice(service):
    srv, vpn, users = service
    await srv.pool_add(SPARE, 'ams', 'reality')
    for owner in (1, 2):
        await owner_with_money(users, owner)

    first = await srv.request(1, 'mini', location='ams', profile='reality')
    assert (await srv.give_from_pool(first.server['_id'])).ok

    second = await srv.request(2, 'mini', location='ams', profile='reality')
    assert not (await srv.give_from_pool(second.server['_id'])).ok, 'машину выдали дважды'


async def test_shares_land_on_the_same_machine(service):
    """Три доли — это один VPS, а не три: сначала досаживаем к своим."""
    srv, vpn, users = service
    await srv.pool_add(SPARE, 'nl', 'reality')
    for owner in (1, 2, 3):
        await owner_with_money(users, owner)
        result = await srv.request(owner, 'share', location='nl', profile='reality')
        assert (await srv.give_from_pool(result.server['_id'])).ok

    assert len(await srv.servers.on_squad(SPARE)) == 3
    assert len(await srv.pool_rows()) == 1, 'машина в запасе должна быть одна'


async def test_a_busy_squad_is_not_taken_into_the_pool(service):
    srv, vpn, users = service
    server = await live_server(service)

    result = await srv.pool_add(server['squad_uuid'], 'ams', 'reality')

    assert not result.ok and result.reason == 'squad_busy'
    assert server['_id'] in result.note


async def test_the_same_machine_is_not_added_twice(service):
    srv, vpn, users = service
    assert (await srv.pool_add(SPARE, 'ams', 'reality')).ok

    again = await srv.pool_add(SPARE, 'ams', 'reality')

    assert not again.ok and again.reason == 'already_in_pool'


async def test_the_queue_counts_machines_not_requests(service):
    """Три доли в одной локации — один VPS. Список из трёх строк заставил бы
    поднять три."""
    srv, vpn, users = service
    for owner in (1, 2, 3):
        await owner_with_money(users, owner)
        await srv.request(owner, 'share', location='nl', profile='reality')

    queue = await srv.queue()

    assert len(queue) == 1
    assert queue[0]['count'] == 3 and queue[0]['machines'] == 1
    assert queue[0]['ready'] == 0


async def test_the_queue_separates_locations_and_protocols(service):
    srv, vpn, users = service
    await owner_with_money(users, 1)
    await owner_with_money(users, 2)
    await srv.request(1, 'mini', location='ams', profile='reality')
    await srv.request(2, 'mini', location='tyo', profile='grpc')

    queue = await srv.queue()

    assert len(queue) == 2
    assert all(group['machines'] == 1 for group in queue)


async def test_the_queue_marks_what_the_spare_covers(service):
    srv, vpn, users = service
    await srv.pool_add(SPARE, 'ams', 'reality')
    for owner in (1, 2):
        await owner_with_money(users, owner)
        await srv.request(owner, 'mini', location='ams', profile='reality')

    group = (await srv.queue())[0]

    assert group['count'] == 2 and group['ready'] == 1
    assert group['machines'] == 1, 'вторая заявка требует ещё одну машину'


# ── ссылка для роутера ──────────────────────────────────────────────────────
#
# Роутер не умеет обновлять подписку, ему нужна прямая ссылка. И только там,
# где протокол это позволяет: Hysteria2 в прошивках почти не встречается.

async def test_router_link_is_only_for_the_right_profiles(service):
    srv, vpn, users = service
    server = await live_server(service)
    vpn.keys = ['vless://key#node-ams']

    links, _ = await srv.router_links(server, 1)
    assert links == ['vless://key#node-ams'], 'на gRPC ссылка должна быть'

    await srv.servers.set(server['_id'], profile='hysteria2')
    hysteria = await srv.servers.get(server['_id'])

    links, note = await srv.router_links(hysteria, 1)
    assert not links and 'TCP Reality' in note


async def test_the_link_of_our_own_node_comes_first(service):
    """Ссылок в подписке много — человеку нужна та, что ведёт на его сервер."""
    srv, vpn, users = service
    server = await live_server(service)
    vpn.keys = ['vless://other#Общий-Франкфурт', 'vless://mine#Node-Ams-1',
                'ss://mine#Node-Ams-1']

    async def nodes(squad):
        assert squad == server['squad_uuid']
        return [{'nodeName': 'Node-Ams-1'}]

    vpn.squad_nodes = nodes
    links, note = await srv.router_links(server, 1)

    assert links == ['vless://mine#Node-Ams-1'], 'ss:// роутеру не годится'
    assert not note


async def test_without_a_node_name_the_last_link_wins(service):
    """Личный сервер добавляется после общих и идёт в подписке последним."""
    srv, vpn, users = service
    server = await live_server(service)
    vpn.keys = ['vless://a@one.example:443#Общий', 'vless://b@two.example:443#Мой']

    links, note = await srv.router_links(server, 1)

    assert links == ['vless://b@two.example:443#Мой']
    assert 'Мой' in note, 'по какой подписи выбрали — должно быть видно'


async def test_service_banners_are_not_offered_as_a_server(service):
    """В подписке подсказки оформлены ссылками; роутер по ним не поднимется."""
    srv, vpn, users = service
    server = await live_server(service)
    vpn.keys = [
        'vless://a@31.76.119.87:443#%F0%9F%87%A9%F0%9F%87%AA%20%D0%93%D0%B5%D1%80'
        '%D0%BC%D0%B0%D0%BD%D0%B8%D1%8F-1',
        'vless://b@11111:443#%E2%AC%86%EF%B8%8F%D0%9E%D0%B1%D1%85%D0%BE%D0%B4%20LTE',
        'vless://c@web.max.ru:443#%E2%AC%87%EF%B8%8F%D0%94%D0%BE%D0%BF%D0%BE%D0%BB'
        '%D0%BD%D0%B8%D1%82%D0%B5%D0%BB%D1%8C%D0%BD%D1%8B%D0%B5%E2%AC%87%EF%B8%8F',
    ]

    links, note = await srv.router_links(server, 1)

    assert links == [vpn.keys[0]], 'выбрана строка-подсказка, а не сервер'
    assert 'Германия-1' in note


def test_banner_recognition():
    assert is_banner_link('vless://x@11111:443#Сервер'), 'адреса 11111 не бывает'
    assert is_banner_link('vless://x@web.max.ru:443#%E2%AC%87%EF%B8%8F%D0%94%D0%BE%D0%BF')
    assert not is_banner_link('vless://x@31.76.119.87:443#Германия-1')
    assert not is_banner_link('vless://x@de1.example.com:443#Германия-1')


async def test_a_panel_without_vless_says_so(service):
    srv, vpn, users = service
    server = await live_server(service)
    vpn.keys = ['ss://only-this']

    links, note = await srv.router_links(server, 1)

    assert not links and 'VLESS' in note


async def test_a_broken_panel_does_not_raise(service):
    srv, vpn, users = service
    server = await live_server(service)

    async def broken(uuid, user_id=None):
        raise RuntimeError('HTTP 500')

    vpn.connection_keys = broken
    links, note = await srv.router_links(server, 1)

    assert not links and 'HTTP 500' in note


# ── участник без своей подписки ─────────────────────────────────────────────
#
# Смысл тарифа в том, что друзьям не нужна своя подписка. Но и уносить её с
# собой при выходе они не должны.

async def test_member_without_a_subscription_gets_one_for_the_paid_period(service):
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': '', 'shortUuid': ''}})

    invite = await srv.invite(server['_id'], 1)
    assert (await srv.join(invite.reason, 2)).ok

    member = await users.get(2)
    assert member['vpn']['uuid'], 'подписку в панели не завели'
    assert vpn.state['u-2']['expireAt'] > now()
    # только сервер друга и ничего больше
    assert vpn.state['u-2']['squads'] == [SQUAD]


async def test_such_member_loses_access_on_leaving(service):
    """Иначе гость на день уносит рабочую подписку до конца месяца владельца."""
    srv, vpn, users = service
    server = await live_server(service)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': '', 'shortUuid': ''}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    await srv.leave(server['_id'], 2)

    assert vpn.state['u-2']['squads'] == []
    assert vpn.state['u-2']['expireAt'] <= now(), 'подписка осталась действующей'


async def test_member_with_his_own_subscription_keeps_it(service):
    """А у того, кто платит сам, ничего отбирать нельзя."""
    srv, vpn, users = service
    server = await live_server(service)
    mine = now() + timedelta(days=100)
    await users.create({'user_data': {'user_id': 2}, 'info': {'balance': 0},
                        'vpn': {'uuid': 'u-2', 'shortUuid': 's-2', 'expireAt': mine,
                                'activeInternalSquads': ['общий']}})
    invite = await srv.invite(server['_id'], 1)
    await srv.join(invite.reason, 2)

    await srv.leave(server['_id'], 2)

    assert vpn.state['u-2']['squads'] == ['общий']
    doc = await users.get(2)
    assert parse_dt(doc['vpn']['expireAt']) == mine
