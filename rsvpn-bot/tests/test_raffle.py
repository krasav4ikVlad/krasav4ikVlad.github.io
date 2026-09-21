"""Билеты розыгрыша: за что они даются и за что нет.

Розыгрыш нельзя пересчитать после того, как приз уехал, поэтому правило
проверяется целиком. Источников билета два — новый приглашённый друг и
своя покупка, — и главное здесь, что ни один нельзя получить бесплатно:
билет даёт только купленная подписка, а не деньги на балансе.
"""

from datetime import timedelta

import pytest

from app.admin import raffle as admin
from app.core.time import now
from app.domain import raffle as domain
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import raffle

from tests.test_admin_panel import ADMIN, admin_env, message  # noqa: F401

START = domain.parse_day('01.10.2026')
END = domain.parse_day('22.10.2026', end=True)


@pytest.fixture
def repos(db):
    return BalanceLogRepository(db['balance_log']), UsersRepository(db['users'])


async def person(users, user_id: int, referrer=None, *, alive: bool = True) -> None:
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'referrer': referrer},
        'info': {'balance': 0},
        'vpn': {'uuid': f'u-{user_id}',
                'expireAt': now() + timedelta(days=30 if alive else -30)},
    })


async def bought(journal, user_id: int, *, months: int = 1, day: int = 5,
                 month: int = 10, kind: str = 'plan', price: int = 150) -> None:
    """Покупка подписки — единственное, что даёт билет."""
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -price, 'kind': kind,
        'at': START.replace(month=month, day=day),
        'meta': {'plan': f'{months}month', 'days': months * 30},
        'description': 'Покупка подписки'})


async def topped_up(journal, user_id: int, amount: int = 1000,
                    day: int = 5) -> None:
    """Пополнение баланса — билетов не даёт вовсе."""
    await journal.col.insert_one({
        'user_id': user_id, 'amount': amount, 'kind': 'topup',
        'at': START.replace(day=day), 'meta': {}, 'description': 'Пополнение'})


async def collect(repos, **kwargs):
    journal, users = repos
    return await raffle.collect(journal, users, start=START, end=END, **kwargs)


# ── месяцы ──────────────────────────────────────────────────────────────────
def test_months_are_counted_by_thirty_days():
    assert raffle.months(30) == 1 and raffle.months(180) == 6


def test_less_than_a_month_is_zero_not_almost_a_month():
    assert raffle.months(7) == 0 and raffle.months(0) == 0


# ── билеты за себя ──────────────────────────────────────────────────────────
async def test_own_subscription_gives_a_ticket_per_month(repos, db):
    """Купил полгода — шесть билетов."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=6)

    data = await collect(repos)

    assert data['tickets'] == 6
    assert data['participants'][0]['user_id'] == 1


async def test_a_renewal_counts_the_same_as_a_purchase(repos, db):
    """Акция должна двигать и продления — их гораздо больше."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=3, kind='renewal')

    assert (await collect(repos))['tickets'] == 3


async def test_two_purchases_add_up(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=1, day=3)
    await bought(journal, 1, months=3, day=15)

    assert (await collect(repos))['tickets'] == 4


async def test_topping_up_the_balance_gives_nothing(repos, db):
    """Деньги на балансе — ещё не подписка."""
    journal, users = repos
    await person(users, 1)
    await topped_up(journal, 1, 5000)

    assert (await collect(repos))['tickets'] == 0


async def test_a_purchase_before_the_contest_gives_nothing(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=6, month=9, day=20)

    assert (await collect(repos))['tickets'] == 0


# ── билеты за друга ─────────────────────────────────────────────────────────
async def test_a_new_friend_gives_three_tickets(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1)

    data = await collect(repos)

    # три за друга плюс один самому другу за его же месяц
    assert data['tickets'] == 4
    owner = next(row for row in data['participants'] if row['user_id'] == 1)
    assert owner['tickets'] == 3 and owner['friends'] == 1


async def test_a_second_friend_gives_three_more(repos, db):
    journal, users = repos
    await person(users, 1)
    for friend_id in (20, 21):
        await person(users, friend_id, referrer=1)
        await bought(journal, friend_id, months=1)

    owner = next(row for row in (await collect(repos))['participants']
                 if row['user_id'] == 1)

    assert owner['tickets'] == 6 and owner['friends'] == 2


async def test_the_same_friend_buying_again_gives_nothing_more(repos, db):
    """Иначе билеты набирались бы на одном и том же человеке."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1, day=3)
    await bought(journal, 20, months=1, day=17, kind='renewal')

    owner = next(row for row in (await collect(repos))['participants']
                 if row['user_id'] == 1)

    assert owner['tickets'] == 3 and owner['friends'] == 1


async def test_an_old_friend_renewing_gives_nothing(repos, db):
    """Друг покупал и раньше — он не новый."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1, month=9, day=10)
    await bought(journal, 20, months=1, day=5, kind='renewal')

    data = await collect(repos)
    owners = [row['user_id'] for row in data['participants']]

    assert 1 not in owners
    assert data['skipped'] == {domain.NOT_NEW: 1}


async def test_a_friend_without_an_inviter_gives_nothing(repos, db):
    journal, users = repos
    await person(users, 20, referrer=None)
    await bought(journal, 20, months=1)

    assert (await collect(repos))['skipped'] == {domain.NO_REFERRER: 1}


async def test_inviting_yourself_gives_nothing(repos, db):
    journal, users = repos
    await person(users, 20, referrer=20)
    await bought(journal, 20, months=1)

    assert (await collect(repos))['skipped'] == {domain.SELF_INVITE: 1}


async def test_an_inviter_who_is_not_in_the_base_gives_nothing(repos, db):
    journal, users = repos
    await person(users, 20, referrer=777777)
    await bought(journal, 20, months=1)

    assert (await collect(repos))['skipped'] == {domain.UNKNOWN_REFERRER: 1}


async def test_a_dead_subscription_of_a_friend_loses_the_tickets(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1, alive=False)
    await bought(journal, 20, months=1)

    assert (await collect(repos))['skipped'] == {domain.NOT_ACTIVE: 1}


async def test_the_friend_keeps_his_own_tickets_anyway(repos, db):
    """Пригласивший ничего не получил, но сам покупатель — получил своё."""
    journal, users = repos
    await person(users, 20, referrer=None)
    await bought(journal, 20, months=2)

    data = await collect(repos)

    assert data['tickets'] == 2
    assert data['participants'][0]['user_id'] == 20


# ── нумерация ───────────────────────────────────────────────────────────────
async def test_each_ticket_is_its_own_line(repos, db):
    """Розыгрыш идёт по номерам: «билет №17» должен означать одного человека."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=3)

    rows = (await collect(repos))['rows']

    assert [row['ticket'] for row in rows] == [1, 2, 3]
    assert {row['owner'] for row in rows} == {1}


async def test_tickets_are_numbered_by_the_date_of_purchase(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 2)
    await bought(journal, 2, months=1, day=3)
    await bought(journal, 1, months=1, day=9)

    rows = (await collect(repos))['rows']

    assert [row['owner'] for row in rows] == [2, 1]


# ── настройки ───────────────────────────────────────────────────────────────
async def test_the_price_of_a_friend_is_a_setting(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1)

    data = await collect(repos, friend_tickets=10)
    owner = next(row for row in data['participants'] if row['user_id'] == 1)

    assert owner['tickets'] == 10


async def test_a_short_subscription_of_a_friend_can_be_refused(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1)

    assert (await collect(repos, min_months=3))['skipped'] == {domain.TOO_SHORT: 1}


# ── экран и выгрузка ────────────────────────────────────────────────────────
async def test_the_summary_splits_friends_from_own(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 1, months=2)
    await bought(journal, 20, months=1)

    text = admin.summary(await collect(repos))

    assert 'за друзей 3, за свои подписки 3' in text


async def test_the_summary_survives_an_empty_contest(repos, db):
    assert 'ни одного' in admin.summary(await collect(repos))


async def test_the_csv_has_a_line_per_ticket(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=3)

    text = admin.to_csv(await collect(repos)).decode('utf-8-sig')
    lines = [line for line in text.splitlines() if line]

    assert lines[0].startswith('билет;дата')
    assert len(lines) == 4


async def test_the_csv_opens_in_excel_without_fixing(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=1)

    raw = admin.to_csv(await collect(repos))

    assert raw.startswith(b'\xef\xbb\xbf') and b';' in raw


async def test_the_file_is_named_after_the_period(repos, db):
    assert admin.filename(await collect(repos)) == 'raffle-01.10.2026-22.10.2026.csv'


def test_ticket_counts_are_declined_properly():
    assert (admin._tickets(1), admin._tickets(2), admin._tickets(5)) == (
        'билет', 'билета', 'билетов')


# ── подарок за порог ────────────────────────────────────────────────────────
async def test_the_threshold_gift_goes_to_those_who_reached_it(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=6)
    await person(users, 2)
    await bought(journal, 2, months=1)

    winners = admin.reached(await collect(repos), 3)

    assert [row['user_id'] for row in winners] == [1]


async def test_a_switched_off_threshold_gives_the_gift_to_nobody(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=6)

    assert admin.reached(await collect(repos), 0) == []


# ── команды целиком ─────────────────────────────────────────────────────────
async def test_the_command_answers_with_the_summary(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')

    await dp.feed_update(bot, message('/raffle'))

    assert 'Розыгрыш' in session.last_text


async def test_the_command_says_when_the_dates_are_not_set(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/raffle'))

    assert 'Не вижу даты' in session.last_text


async def test_the_table_comes_as_a_file(admin_env):
    dp, bot, session, container = admin_env
    await container.users.create({
        'user_data': {'user_id': 900, 'referrer': None, 'date_joined': now()},
        'vpn': {'uuid': 'u-900', 'expireAt': now() + timedelta(days=10)}})
    await container.db['balance_log'].insert_one({
        'user_id': 900, 'amount': -150, 'kind': 'plan',
        'at': START.replace(day=4), 'meta': {'days': 30},
        'description': 'Покупка подписки'})

    await dp.feed_update(bot, message('/raffletickets 01.10.2026 22.10.2026'))

    assert [name for name, _ in session.calls if name == 'SendDocument']


async def test_an_empty_period_does_not_send_an_empty_file(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/raffletickets 01.10.2026 22.10.2026'))

    assert not [name for name, _ in session.calls if name == 'SendDocument']
    assert 'нечего' in session.last_text


async def test_the_gift_list_names_everyone_to_credit(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')
    await container.settings.set('raffle.bonus_tickets', 2)
    await container.users.create({
        'user_data': {'user_id': ADMIN.id},
        'vpn': {'uuid': 'u-1', 'expireAt': now() + timedelta(days=10)}})
    await container.db['balance_log'].insert_one({
        'user_id': ADMIN.id, 'amount': -450, 'kind': 'plan',
        'at': START.replace(day=4), 'meta': {'days': 90},
        'description': 'Покупка подписки'})

    await dp.feed_update(bot, message('/rafflebonus'))

    assert str(ADMIN.id) in session.last_text
