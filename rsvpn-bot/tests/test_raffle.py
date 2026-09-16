"""Билеты розыгрыша: за что они даются и за что нет.

Розыгрыш нельзя пересчитать после того, как приз уехал, поэтому правило
проверяется здесь целиком — особенно то, что билет нельзя получить бесплатно
и нельзя получить дважды за одного друга.
"""

from datetime import timedelta

import pytest

from app.admin import raffle as admin
from app.core.time import now
from app.domain import raffle as domain
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services import raffle

from tests.test_admin_panel import ADMIN, admin_env, message  # noqa: F401

START = domain.parse_day('01.10.2026')
END = domain.parse_day('22.10.2026', end=True)
MIN = 199


@pytest.fixture
def repos(db):
    return PaymentsRepository(db['payments']), UsersRepository(db['users'])


async def friend(users, friend_id: int, referrer: int | None, *, alive: bool = True,
                 joined_days_ago: int = 1, uuid: str = 'u') -> None:
    await users.create({
        'user_data': {'user_id': friend_id, 'username': f'f{friend_id}',
                      'referrer': referrer,
                      'date_joined': now() - timedelta(days=joined_days_ago)},
        'info': {'balance': 0},
        'vpn': {'uuid': f'{uuid}-{friend_id}',
                'expireAt': now() + timedelta(days=30 if alive else -30)},
    })


async def paid(payments, user_id: int, amount: int, day: int, *, hour: int = 12,
               minute: int = 0, month: int = 10, payload: dict | None = None,
               status: str = 'done') -> None:
    """Оплата в конкретный день — даты здесь и есть предмет проверки."""
    await payments.col.insert_one({
        'txid': f'tx-{user_id}-{month}-{day}-{hour}-{minute}',
        'user_id': user_id, 'amount': amount, 'status': status,
        'payload': payload or {},
        'created_at': START.replace(month=month, day=day, hour=hour, minute=minute),
    })


async def collect(repos, **kwargs):
    payments, users = repos
    return await raffle.collect(payments, users, start=START, end=END,
                                min_payment=MIN, **kwargs)


# ── период ──────────────────────────────────────────────────────────────────
def test_the_last_day_of_the_contest_counts_whole():
    """«Акция до 22 октября» для человека включает всё 22 октября."""
    end = domain.parse_day('22.10.2026', end=True)

    assert (end.day, end.hour, end.minute) == (22, 23, 59)


def test_the_first_day_starts_at_midnight():
    start = domain.parse_day('01.10.2026')

    assert (start.day, start.hour) == (1, 0)


def test_dates_are_understood_both_ways():
    assert domain.parse_day('01.10.2026') == domain.parse_day('2026-10-01')


def test_a_missing_date_is_not_a_date():
    assert domain.parse_day('') is None and domain.parse_day('когда-нибудь') is None


# ── за что даётся билет ─────────────────────────────────────────────────────
async def test_a_friend_who_paid_gives_a_ticket(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=5)

    data = await collect(repos)

    assert data['tickets'] == 1
    assert data['rows'][0]['ticket'] == 1
    assert data['participants'] == [{'user_id': 1, 'username': 'f1',
                                     'tickets': 1, 'amount': 300}]


async def test_a_registration_without_money_gives_nothing(repos, db):
    """Иначе телефон разыгрывается среди пустых аккаунтов."""
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)

    assert (await collect(repos))['tickets'] == 0


async def test_a_payment_below_the_minimum_gives_nothing(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 100, day=5)

    data = await collect(repos)

    assert data['tickets'] == 0
    assert data['skipped'] == {domain.TOO_SMALL: 1}


async def test_two_small_payments_add_up_to_a_ticket(repos, db):
    """Человек пополняет как ему удобно; складывать — честнее, чем требовать
    одну оплату нужного размера."""
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 100, day=5)
    await paid(payments, 20, 150, day=6)

    data = await collect(repos)

    assert data['tickets'] == 1 and data['rows'][0]['amount'] == 250


async def test_one_friend_is_one_ticket_no_matter_how_often_he_pays(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    for day in (5, 9, 14):
        await paid(payments, 20, 300, day=day)

    assert (await collect(repos))['tickets'] == 1


async def test_a_friend_without_an_inviter_gives_nothing(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=None)
    await paid(payments, 20, 300, day=5)

    data = await collect(repos)

    assert data['tickets'] == 0 and data['skipped'] == {domain.NO_REFERRER: 1}


async def test_inviting_yourself_gives_nothing(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=20)
    await paid(payments, 20, 300, day=5)

    assert (await collect(repos))['skipped'] == {domain.SELF_INVITE: 1}


async def test_an_inviter_who_is_not_in_the_base_gives_nothing(repos, db):
    """Метку могли подставить руками в ссылке — участника с таким id нет."""
    payments, users = repos
    await friend(users, 20, referrer=777777)
    await paid(payments, 20, 300, day=5)

    assert (await collect(repos))['skipped'] == {domain.UNKNOWN_REFERRER: 1}


# ── что в зачёт не идёт ─────────────────────────────────────────────────────
async def test_a_payment_before_the_contest_gives_nothing(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=20, month=9)

    assert (await collect(repos))['tickets'] == 0


async def test_an_old_payer_renewing_gives_nothing(repos, db):
    """Главное правило: акция платит за новых платящих. Иначе билеты соберёт
    тот, у кого пятьдесят старых друзей просто продлились."""
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=15, month=9)     # заплатил до акции
    await paid(payments, 20, 300, day=5)               # продлился во время

    data = await collect(repos)

    assert data['tickets'] == 0 and data['skipped'] == {}


async def test_a_friend_who_registered_earlier_but_paid_now_counts(repos, db):
    """Расталкивать своих же зарегистрированных, но не заплативших — самая
    дешёвая выручка в акции, и она должна вознаграждаться."""
    payments, users = repos
    await friend(users, 20, referrer=1, joined_days_ago=400)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=5)

    assert (await collect(repos))['tickets'] == 1


async def test_an_unfinished_payment_is_not_money(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=5, status='new')

    assert (await collect(repos))['tickets'] == 0


async def test_a_dead_subscription_loses_the_ticket(repos, db):
    """Без этого выгодно оплатить минимум на десять аккаунтов и всё бросить."""
    payments, users = repos
    await friend(users, 20, referrer=1, alive=False)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=5)

    data = await collect(repos)

    assert data['tickets'] == 0 and data['skipped'] == {domain.NOT_ACTIVE: 1}


async def test_the_activity_check_can_be_switched_off(repos, db):
    payments, users = repos
    await friend(users, 20, referrer=1, alive=False)
    await friend(users, 1, referrer=None)
    await paid(payments, 20, 300, day=5)

    assert (await collect(repos, require_active=False))['tickets'] == 1


# ── нумерация ───────────────────────────────────────────────────────────────
async def test_tickets_are_numbered_by_the_date_of_payment(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    for friend_id, day in ((21, 9), (22, 3), (23, 6)):
        await friend(users, friend_id, referrer=1)
        await paid(payments, friend_id, 300, day=day)

    rows = [row for row in (await collect(repos))['rows'] if row['ticket']]

    assert [(row['ticket'], row['friend_id']) for row in rows] == [
        (1, 22), (2, 23), (3, 21)]


async def test_rejected_payments_stay_in_the_table_without_a_number(repos, db):
    """Иначе на вопрос «почему у меня не засчиталось» отвечать будет нечем."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 21, referrer=1)
    await friend(users, 22, referrer=None)
    await paid(payments, 21, 300, day=5)
    await paid(payments, 22, 300, day=6)

    rows = (await collect(repos))['rows']

    assert len(rows) == 2
    assert sorted((row['ticket'], row['why']) for row in rows) == [
        (0, domain.NO_REFERRER), (1, '')]


# ── подозрения ──────────────────────────────────────────────────────────────
async def test_three_payments_within_an_hour_are_flagged(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    for index, minute in enumerate((0, 10, 40)):
        await friend(users, 30 + index, referrer=1)
        await paid(payments, 30 + index, 300, day=5, minute=minute)

    data = await collect(repos)

    assert data['flagged'] == 3
    assert all(domain.BATCH in row['flags'] for row in data['rows'])


async def test_the_same_three_spread_over_days_are_not(repos, db):
    """Честный участник приводит друзей неделю — пометка не должна быть
    наказанием за успех."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    for index, day in enumerate((3, 7, 15)):
        await friend(users, 30 + index, referrer=1)
        await paid(payments, 30 + index, 300, day=day)

    assert (await collect(repos))['flagged'] == 0


async def test_one_wallet_on_two_accounts_is_flagged(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 41, referrer=1)
    await friend(users, 42, referrer=1)
    await paid(payments, 41, 300, day=3, payload={'email': 'One@mail.ru'})
    await paid(payments, 42, 300, day=12, payload={'email': 'one@mail.ru'})

    data = await collect(repos)

    assert data['flagged'] == 2
    assert all(domain.SAME_PAYER in row['flags'] for row in data['rows'])


async def test_different_wallets_are_not_flagged(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 41, referrer=1)
    await friend(users, 42, referrer=1)
    await paid(payments, 41, 300, day=3, payload={'email': 'one@mail.ru'})
    await paid(payments, 42, 300, day=12, payload={'email': 'two@mail.ru'})

    assert (await collect(repos))['flagged'] == 0


async def test_a_payment_without_any_identity_is_not_a_match(repos, db):
    """Пустой отпечаток есть у всех — иначе «совпало» было бы у каждого."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 41, referrer=1)
    await friend(users, 42, referrer=1)
    await paid(payments, 41, 300, day=3)
    await paid(payments, 42, 300, day=12)

    assert (await collect(repos))['flagged'] == 0


async def test_paid_but_never_connected_is_flagged(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await users.create({
        'user_data': {'user_id': 50, 'referrer': 1, 'date_joined': now()},
        'vpn': {'uuid': '', 'expireAt': now() + timedelta(days=10)},
    })
    await paid(payments, 50, 300, day=5)

    data = await collect(repos)

    assert data['tickets'] == 1
    assert domain.NEVER_CONNECTED in data['rows'][0]['flags']


# ── деньги ──────────────────────────────────────────────────────────────────
async def test_the_report_says_how_much_the_contest_brought(repos, db):
    """Без этого числа нельзя сказать, окупился ли приз."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 21, referrer=1)
    await friend(users, 22, referrer=1)
    await paid(payments, 21, 500, day=3)
    await paid(payments, 22, 900, day=4)

    assert (await collect(repos))['revenue'] == 1400


async def test_only_counted_tickets_are_counted_as_revenue(repos, db):
    payments, users = repos
    await friend(users, 21, referrer=None)
    await paid(payments, 21, 500, day=3)

    assert (await collect(repos))['revenue'] == 0


# ── таблица ─────────────────────────────────────────────────────────────────
async def test_the_csv_has_a_line_per_payment_and_a_header(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 21, referrer=1)
    await friend(users, 22, referrer=None)
    await paid(payments, 21, 300, day=5)
    await paid(payments, 22, 300, day=6)

    text = admin.to_csv(await collect(repos)).decode('utf-8-sig')
    lines = [line for line in text.splitlines() if line]

    assert lines[0].startswith('билет;дата')
    assert len(lines) == 3


async def test_the_csv_opens_in_excel_without_fixing(repos, db):
    """BOM и точка с запятой: без них русские заголовки становятся
    кракозябрами, а строка — одной ячейкой."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 21, referrer=1)
    await paid(payments, 21, 300, day=5)

    raw = admin.to_csv(await collect(repos))

    assert raw.startswith(b'\xef\xbb\xbf')
    assert b';' in raw


async def test_the_file_is_named_after_the_period(repos, db):
    data = await collect(repos)

    assert admin.filename(data) == 'raffle-01.10.2026-22.10.2026.csv'


async def test_the_summary_survives_an_empty_contest(repos, db):
    """Пустая сводка — нормальный ответ в первый день, и она не должна падать."""
    text = admin.summary(await collect(repos))

    assert 'ни одного' in text


# ── подарок за порог ────────────────────────────────────────────────────────
async def three_friends(repos, owner: int, count: int, start_id: int) -> None:
    payments, users = repos
    for index in range(count):
        friend_id = start_id + index
        await friend(users, friend_id, referrer=owner)
        await paid(payments, friend_id, 300, day=3 + index * 3)


async def test_the_threshold_gift_goes_to_those_who_reached_it(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await friend(users, 2, referrer=None)
    await three_friends(repos, owner=1, count=3, start_id=60)
    await three_friends(repos, owner=2, count=2, start_id=70)

    winners = admin.reached(await collect(repos), 3)

    assert [item['user_id'] for item in winners] == [1]


async def test_more_than_the_threshold_still_counts(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await three_friends(repos, owner=1, count=5, start_id=60)

    assert len(admin.reached(await collect(repos), 3)) == 1


async def test_a_switched_off_threshold_gives_the_gift_to_nobody(repos, db):
    """Ноль — это «не дарить», а не «дарить всем»: обратное прочтение стоило
    бы подарка каждому, у кого есть хоть один билет."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await three_friends(repos, owner=1, count=3, start_id=60)

    assert admin.reached(await collect(repos), 0) == []


async def test_the_summary_says_how_many_reached_the_threshold(repos, db):
    """Единственный расход акции, который не разыгрывается, а причитается, —
    его цену надо видеть до розыгрыша."""
    payments, users = repos
    await friend(users, 1, referrer=None)
    await three_friends(repos, owner=1, count=3, start_id=60)

    text = admin.summary(await collect(repos), bonus_tickets=3, bonus_days=7)

    assert 'Порог 3 билета прошли: <b>1</b> — им +7 дн.' in text


async def test_the_summary_keeps_quiet_when_the_gift_is_off(repos, db):
    payments, users = repos
    await friend(users, 1, referrer=None)
    await three_friends(repos, owner=1, count=3, start_id=60)

    assert 'Порог' not in admin.summary(await collect(repos))


async def test_the_gift_list_names_everyone_to_credit(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    for index in range(3):
        friend_id = 700 + index
        await container.users.create({
            'user_data': {'user_id': friend_id, 'referrer': ADMIN.id,
                          'date_joined': now()},
            'vpn': {'uuid': f'u-{friend_id}', 'expireAt': now() + timedelta(days=10)},
        })
        await container.db['payments'].insert_one({
            'txid': f'tx-{friend_id}', 'user_id': friend_id, 'amount': 300,
            'status': 'done', 'payload': {}, 'created_at': START.replace(day=4 + index),
        })

    await dp.feed_update(bot, message('/rafflebonus'))

    assert str(ADMIN.id) in session.last_text


async def test_the_gift_list_says_when_nobody_reached_it(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')

    await dp.feed_update(bot, message('/rafflebonus'))

    assert 'никто не прошёл' in session.last_text


async def test_the_gift_list_says_when_the_gift_is_switched_off(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.bonus_tickets', 0)

    await dp.feed_update(bot, message('/rafflebonus'))

    assert 'выключен' in session.last_text


# ── команды целиком ─────────────────────────────────────────────────────────
async def test_the_command_answers_with_the_summary(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')

    await dp.feed_update(bot, message('/raffle'))

    assert 'Розыгрыш' in session.last_text


async def test_the_command_says_when_the_dates_are_not_set(admin_env):
    """Без даты акции считать нечего, и молчать об этом нельзя."""
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/raffle'))

    assert 'Не вижу даты' in session.last_text


async def test_the_dates_can_come_from_the_command(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/raffle 01.10.2026 22.10.2026'))

    assert 'Розыгрыш' in session.last_text


async def test_the_table_comes_as_a_file(admin_env):
    dp, bot, session, container = admin_env
    await container.users.create({
        'user_data': {'user_id': 900, 'referrer': ADMIN.id, 'date_joined': now()},
        'vpn': {'uuid': 'u-900', 'expireAt': now() + timedelta(days=10)},
    })
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    await container.db['payments'].insert_one({
        'txid': 'tx1', 'user_id': 900, 'amount': 300, 'status': 'done',
        'created_at': START.replace(day=4), 'payload': {},
    })

    await dp.feed_update(bot, message('/raffletickets 01.10.2026 22.10.2026'))

    assert [name for name, _ in session.calls if name == 'SendDocument']


async def test_an_empty_period_does_not_send_an_empty_file(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/raffletickets 01.10.2026 22.10.2026'))

    assert not [name for name, _ in session.calls if name == 'SendDocument']
    assert 'нечего' in session.last_text


def test_ticket_counts_are_declined_properly():
    assert (admin._tickets(1), admin._tickets(2), admin._tickets(5),
            admin._tickets(11), admin._tickets(21)) == (
        'билет', 'билета', 'билетов', 'билетов', 'билет')
