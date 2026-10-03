"""Кто приходил, пока бот молчал.

Простой сам по себе ничего не записывает, и поэтому список собирается по
косвенным следам. Проверяется, что следы читаются правильно: и окно, и
заблокировавшие, и — отдельно — деньги, которые в этот момент ушли
провайдеру и не дошли до баланса.
"""

from datetime import timedelta

import pytest

from app.admin import outage as admin
from app.core.time import MSK, now
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services import outage

from tests.test_admin_panel import ADMIN, admin_env, callback, message  # noqa: F401

NIGHT = admin._moment('17.09.2026', '02:00')
MORNING = admin._moment('17.09.2026', '09:00')


@pytest.fixture
def repos(db):
    return UsersRepository(db['users']), PaymentsRepository(db['payments'])


def stamp(at) -> str:
    return at.strftime(UsersRepository.TIME_FORMAT)


async def visitor(users, user_id: int, *, at=None, times: int = 1,
                  blocked: bool = False, alive: bool = True) -> None:
    """Человек с записями в журнале — так выглядит обращение в бота."""
    moment = at or NIGHT + timedelta(hours=1)
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}'},
        'growth': {'blocked_bot': True} if blocked else {},
        'vpn': {'expireAt': now() + timedelta(days=30 if alive else -30)},
        'logs': [{'action': UsersRepository.ACTION_USER, 'details': 'menu',
                  'timestamp': stamp(moment + timedelta(minutes=index))}
                 for index in range(times)],
    })


async def payment(payments, user_id: int, amount: int, at, status: str = 'done',
                  provider: str = 'cardlink') -> None:
    await payments.col.insert_one({
        'txid': f'tx-{user_id}-{int(at.timestamp())}', 'user_id': user_id,
        'amount': amount, 'status': status, 'provider': provider,
        'created_at': at, 'payload': {},
    })


# ── окно ────────────────────────────────────────────────────────────────────
def test_without_arguments_the_window_is_the_last_half_day():
    moment = now()

    start, end = admin.parse_window('', moment)

    assert end == moment and (end - start).total_seconds() == 12 * 3600


def test_a_number_means_hours_back():
    moment = now()

    start, end = admin.parse_window('3', moment)

    assert (end - start).total_seconds() == 3 * 3600


def test_a_date_with_two_times_is_an_exact_window():
    start, end = admin.parse_window('17.09.2026 02:00 09:00')

    assert (start.hour, end.hour, start.day) == (2, 9, 17)


def test_a_window_through_midnight_does_not_go_backwards():
    """«С 23:00 до 09:00» — это ночь, а не минус четырнадцать часов."""
    start, end = admin.parse_window('16.09.2026 23:00 09:00')

    assert end > start
    assert (start.day, end.day) == (16, 17)


def test_two_dates_and_two_times_work_too():
    start, end = admin.parse_window('16.09.2026 23:00 17.09.2026 09:00')

    assert (start.day, end.day, end.hour) == (16, 17, 9)


def test_nonsense_is_not_guessed():
    assert admin.parse_window('вчера ночью') == (None, None)
    assert admin.parse_window('0') == (None, None)


# ── кто попал в окно ────────────────────────────────────────────────────────
async def test_someone_who_wrote_in_the_window_is_found(repos, db):
    users, payments = repos
    await visitor(users, 10)

    data = await outage.visitors(users, NIGHT, MORNING)

    assert [item['user_id'] for item in data['people']] == [10]


async def test_someone_who_wrote_outside_the_window_is_not(repos, db):
    users, payments = repos
    await visitor(users, 10, at=NIGHT - timedelta(hours=5))

    assert (await outage.visitors(users, NIGHT, MORNING))['total'] == 0


async def test_the_day_before_counts_when_the_window_crosses_midnight(repos, db):
    """Ночной простой почти всегда лежит на двух датах сразу."""
    users, payments = repos
    start = admin._moment('16.09.2026', '23:00')
    await visitor(users, 10, at=admin._moment('16.09.2026', '23:30'))
    await visitor(users, 11, at=admin._moment('17.09.2026', '01:00'))

    data = await outage.visitors(users, start, MORNING)

    assert data['total'] == 2


async def test_repeated_attempts_are_counted(repos, db):
    """Девять попыток за ночь — это не то же самое, что одно нажатие."""
    users, payments = repos
    await visitor(users, 10, times=9)

    row = (await outage.visitors(users, NIGHT, MORNING))['people'][0]

    assert row['hits'] == 9 and row['first'] < row['last']


async def test_those_who_blocked_the_bot_are_counted_apart(repos, db):
    """Иначе «отправлено 400, доставлено 280» выглядит как сбой рассылки."""
    users, payments = repos
    await visitor(users, 10)
    await visitor(users, 11, blocked=True)

    data = await outage.visitors(users, NIGHT, MORNING)

    assert data['total'] == 1 and data['blocked'] == 1


async def test_an_active_subscription_is_visible_in_the_row(repos, db):
    users, payments = repos
    await visitor(users, 10, alive=True)
    await visitor(users, 11, alive=False)

    data = await outage.visitors(users, NIGHT, MORNING)

    assert sorted((item['user_id'], item['active']) for item in data['people']) == [
        (10, True), (11, False)]


async def test_the_busiest_come_first(repos, db):
    users, payments = repos
    await visitor(users, 10, times=2)
    await visitor(users, 11, times=7)

    data = await outage.visitors(users, NIGHT, MORNING)

    assert [item['user_id'] for item in data['people']] == [11, 10]


async def test_the_list_stops_at_its_limit_and_says_so(repos, db):
    users, payments = repos
    for user_id in range(10, 15):
        await visitor(users, user_id)

    data = await outage.visitors(users, NIGHT, MORNING, limit=3)

    assert data['total'] == 3 and data['stopped'] is True


# ── деньги за то же время ───────────────────────────────────────────────────
async def test_payments_in_the_window_are_counted(repos, db):
    users, payments = repos
    await payment(payments, 10, 500, NIGHT + timedelta(hours=1))

    money = await outage.payments_in(payments, NIGHT, MORNING)

    assert money['total'] == 1 and money['done'] == 1 and money['stuck'] == []


async def test_a_payment_that_never_got_credited_is_named(repos, db):
    """Хуже неотвеченного нажатия: деньги ушли, а на баланс не легли."""
    users, payments = repos
    await payment(payments, 10, 450, NIGHT + timedelta(hours=1), status='new')
    await payment(payments, 11, 300, NIGHT + timedelta(hours=2),
                  status='credit_failed')

    money = await outage.payments_in(payments, NIGHT, MORNING)

    assert money['done'] == 0 and money['lost'] == 750
    assert [row['user_id'] for row in money['stuck']] == [10, 11]


async def test_payments_outside_the_window_are_not_ours(repos, db):
    users, payments = repos
    await payment(payments, 10, 500, NIGHT - timedelta(hours=3), status='new')

    assert (await outage.payments_in(payments, NIGHT, MORNING))['total'] == 0


# ── отчёт ───────────────────────────────────────────────────────────────────
async def test_the_report_puts_lost_money_in_front(repos, db):
    users, payments = repos
    await visitor(users, 10)
    await payment(payments, 10, 450, NIGHT + timedelta(hours=1), status='new')

    text = admin.summary(await outage.report(users, payments, NIGHT, MORNING))

    assert 'Не зачислено: 1 на 450₽' in text
    assert '<code>10</code>' in text


async def test_an_empty_report_explains_itself(repos, db):
    """Пустой список после простоя — обычное дело, и это не поломка."""
    users, payments = repos

    text = admin.summary(await outage.report(users, payments, NIGHT, MORNING))

    assert 'обращений в журнале нет' in text
    assert 'временем перезапуска' in text


async def test_there_is_no_button_when_there_is_nobody_to_write_to(repos, db):
    users, payments = repos

    data = await outage.report(users, payments, NIGHT, MORNING)

    assert admin.keyboard(data).as_markup().inline_keyboard == []


# ── письмо ──────────────────────────────────────────────────────────────────
def outage_click() -> str:
    """Нажатие «Написать этим людям» с ночным окном в кнопке."""
    from app.bot.callbacks import Admin as Adm

    return Adm(act='outg', a=str(int(NIGHT.timestamp())),
               b=str(int(MORNING.timestamp()))).pack()


async def test_the_command_answers_with_the_report(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/outage 2'))

    assert 'пока бот молчал' in session.last_text


async def test_a_broken_window_is_not_guessed(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/outage позавчера'))

    assert 'Не понял окно' in session.last_text


async def test_the_button_hands_the_list_to_the_broadcast(admin_env):
    """Рассылка одна на весь бот: своей отправки здесь нет нарочно."""
    from app.admin.broadcast import Broadcast
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    users = container.users
    await visitor(users, 10)
    await visitor(users, 11)

    await dp.feed_update(bot, callback(Adm(
        act='outg', a=str(int(NIGHT.timestamp())),
        b=str(int(MORNING.timestamp()))).pack()))

    state = dp.fsm.get_context(bot, chat_id=1, user_id=ADMIN.id)
    data = await state.get_data()
    assert await state.get_state() == Broadcast.text.state
    assert sorted(data['recipients']) == [10, 11]


async def test_the_ready_made_apology_comes_with_it(admin_env):
    dp, bot, session, container = admin_env
    await visitor(container.users, 10)

    await dp.feed_update(bot, callback(outage_click()))

    assert any('не отвечал' in text for _, text in session.calls)


async def test_a_prepared_list_is_what_the_broadcast_sends(admin_env):
    """Главная проверка сшивки: рассылка обязана взять готовый список, а не
    пересобрать его запросом по сегментам — сегмента «приходил ночью» нет."""
    from app.admin import broadcast
    from app.bot.callbacks import Admin as Adm
    from app.core import db as names

    dp, bot, session, container = admin_env
    await visitor(container.users, 10)
    await visitor(container.users, 11)
    # Тот, кто ночью не приходил: если рассылка соберёт получателей сама,
    # письмо уйдёт и ему — ровно то, чего быть не должно.
    await container.users.create({'user_data': {'user_id': 12}})

    await dp.feed_update(bot, callback(outage_click()))
    await dp.feed_update(bot, message('Извините за ночь'))
    await dp.feed_update(bot, callback(Adm(act='bcgo').pack()))

    jobs = await container.db[names.BROADCASTS].find({}).to_list(length=10)
    assert sorted(jobs[0]['recipients']) == [10, 11]
    assert jobs[0]['audience'] == 'outage'
