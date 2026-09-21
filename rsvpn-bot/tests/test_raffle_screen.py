"""Экран розыгрыша у человека и выдача призов.

Порог, которого человек не видит, работает вполсилы — поэтому главное
здесь, что билеты на экране считаются по тем же правилам, что и таблица
для розыгрыша. Разойдись они, и правым окажется человек, а не бот.
"""

from datetime import timedelta

import pytest

from app.bot.handlers import raffle as screen
from app.core.time import now
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services import raffle
from app.services import raffle_prizes as prizes
from app.settings.service import SettingsService

START = now() - timedelta(days=3)
END = now() + timedelta(days=4)


@pytest.fixture
def repos(db):
    return PaymentsRepository(db['payments']), UsersRepository(db['users'])


@pytest.fixture
async def settings(db):
    service = SettingsService(db['bot_settings'])
    await service.set('raffle.start', START.strftime('%d.%m.%Y'))
    await service.set('raffle.end', END.strftime('%d.%m.%Y'))
    return service


async def person(users, user_id: int, referrer=None, *, alive: bool = True):
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'referrer': referrer},
        'info': {'balance': 0},
        'vpn': {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                'expireAt': now() + timedelta(days=30 if alive else -30)},
    })


async def paid(payments, user_id: int, amount: int, at) -> None:
    await payments.col.insert_one({
        'txid': f'tx-{user_id}-{at.timestamp()}', 'user_id': user_id,
        'amount': amount, 'status': 'done', 'created_at': at, 'payload': {}})


async def mine(repos, user_id: int = 1, **kwargs):
    payments, users = repos
    return await raffle.for_user(payments, users, user_id, start=START,
                                 end=END, **kwargs)


# ── билеты одного человека ──────────────────────────────────────────────────
async def test_a_friend_who_paid_gives_me_a_ticket(repos, db):
    payments, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await paid(payments, 20, 300, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 1


async def test_a_friend_of_someone_else_does_not(repos, db):
    payments, users = repos
    await person(users, 1)
    await person(users, 20, referrer=999)
    await paid(payments, 20, 300, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 0


async def test_a_friend_who_paid_before_the_contest_does_not(repos, db):
    """То же правило, что и в таблице: считается первая в жизни оплата."""
    payments, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await paid(payments, 20, 300, START - timedelta(days=10))
    await paid(payments, 20, 300, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 0


async def test_a_friend_who_paid_too_little_does_not(repos, db):
    payments, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await paid(payments, 20, 100, now() - timedelta(days=1))

    assert (await mine(repos, min_payment=199))['tickets'] == 0


async def test_a_friend_whose_subscription_died_does_not(repos, db):
    payments, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1, alive=False)
    await paid(payments, 20, 300, now() - timedelta(days=1))

    assert (await mine(repos, require_active=True))['tickets'] == 0


async def test_inviting_yourself_gives_nothing(repos, db):
    payments, users = repos
    await person(users, 1, referrer=1)
    await paid(payments, 1, 300, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 0


async def test_the_screen_matches_the_table(repos, db):
    """Главная проверка: экран и таблица считают одно и то же."""
    payments, users = repos
    await person(users, 1)
    for friend_id in (20, 21, 22):
        await person(users, friend_id, referrer=1)
        await paid(payments, friend_id, 300, now() - timedelta(days=1))
    await person(users, 30, referrer=1, alive=False)
    await paid(payments, 30, 300, now() - timedelta(days=1))

    table = await raffle.collect(payments, users, start=START, end=END,
                                 min_payment=199, require_active=True)
    screen_count = await mine(repos, min_payment=199, require_active=True)

    assert screen_count['tickets'] == table['participants'][0]['tickets'] == 3


# ── когда показывать кнопку ─────────────────────────────────────────────────
async def test_the_button_shows_while_the_contest_runs(settings):
    assert await screen.running(settings) is True


async def test_the_button_hides_without_dates(db):
    assert await screen.running(SettingsService(db['bot_settings'])) is False


async def test_the_button_hides_long_after_the_end(settings):
    """Раздел, который полгода показывает «розыгрыш закончился», — худший
    из возможных."""
    old = now() - timedelta(days=30)
    await settings.set('raffle.start', (old - timedelta(days=7)).strftime('%d.%m.%Y'))
    await settings.set('raffle.end', old.strftime('%d.%m.%Y'))

    assert await screen.running(settings) is False


async def test_the_button_stays_for_a_few_days_after_the_end(settings):
    """Розыгрыш проходит не в ту же минуту — человек придёт смотреть билеты."""
    end = now() - timedelta(days=1)
    await settings.set('raffle.end', end.strftime('%d.%m.%Y'))

    assert await screen.running(settings) is True


# ── разбор списка победителей ───────────────────────────────────────────────
def test_money_and_days_are_told_apart():
    winners, broken = prizes.parse_list('802421217 5000\n802421218 30д')

    assert broken == []
    assert winners == [{'user_id': 802421217, 'amount': 5000, 'kind': 'money'},
                       {'user_id': 802421218, 'amount': 30, 'kind': 'days'}]


def test_days_are_understood_in_words():
    winners, _ = prizes.parse_list('802421217 7 дней')

    assert winners[0]['kind'] == 'days' and winners[0]['amount'] == 7


def test_a_line_that_is_not_understood_is_not_skipped_silently():
    """Молча пропустить строку нельзя — это чей-то приз."""
    winners, broken = prizes.parse_list('802421217 5000\nкому-то что-то')

    assert len(winners) == 1 and broken == ['кому-то что-то']


def test_empty_lines_are_not_errors():
    winners, broken = prizes.parse_list('\n802421217 5000\n\n')

    assert len(winners) == 1 and broken == []


# ── выдача призов ───────────────────────────────────────────────────────────
class Panel:
    def __init__(self, broken: bool = False):
        self.updated: list[tuple] = []
        self.broken = broken

    async def update_subscription(self, ref, **kw):
        if self.broken:
            raise RuntimeError('панель молчит')
        self.updated.append((ref, kw))
        return {}


async def test_money_lands_on_the_balance(repos, db):
    payments, users = repos
    await person(users, 10)

    report = await prizes.award(users, Panel(),
                                [{'user_id': 10, 'amount': 5000, 'kind': 'money'}])

    assert len(report['done']) == 1
    assert (await users.get(10))['info']['balance'] == 5000


async def test_days_move_the_expiry_in_the_panel_and_here(repos, db):
    payments, users = repos
    await person(users, 10)
    before = (await users.get(10))['vpn']['expireAt']
    panel = Panel()

    await prizes.award(users, panel,
                       [{'user_id': 10, 'amount': 30, 'kind': 'days'}])

    after = (await users.get(10))['vpn']['expireAt']
    assert (after - before).days == 30 and panel.updated


async def test_a_refusing_panel_does_not_move_our_date(repos, db):
    """Иначе бот показывал бы срок, которого в панели нет."""
    payments, users = repos
    await person(users, 10)
    before = (await users.get(10))['vpn']['expireAt']

    report = await prizes.award(users, Panel(broken=True),
                                [{'user_id': 10, 'amount': 30, 'kind': 'days'}])

    assert len(report['failed']) == 1
    assert (await users.get(10))['vpn']['expireAt'] == before


async def test_a_prize_is_not_given_twice(repos, db):
    """Команду зовут с телефона, и второе нажатие «на всякий случай» не
    должно удваивать призы."""
    payments, users = repos
    await person(users, 10)
    winners = [{'user_id': 10, 'amount': 5000, 'kind': 'money'}]

    await prizes.award(users, Panel(), winners, mark='raffle_test')
    again = await prizes.award(users, Panel(), winners, mark='raffle_test')

    assert len(again['skipped']) == 1
    assert (await users.get(10))['info']['balance'] == 5000


async def test_an_unknown_person_is_reported_not_swallowed(repos, db):
    payments, users = repos

    report = await prizes.award(users, Panel(),
                                [{'user_id': 999, 'amount': 5000, 'kind': 'money'}])

    assert report['failed'][0]['why'] == 'нет такого пользователя'


async def test_days_without_a_subscription_are_reported(repos, db):
    payments, users = repos
    await users.create({'user_data': {'user_id': 10}, 'vpn': {'uuid': ''}})

    report = await prizes.award(users, Panel(),
                                [{'user_id': 10, 'amount': 30, 'kind': 'days'}])

    assert 'подписки нет' in report['failed'][0]['why']


def test_the_letter_names_the_prize():
    assert 'Ваш приз: 5000₽ на баланс.' in prizes.letter(
        {'amount': 5000, 'kind': 'money'}, 'Поздравляем!')
    assert '30 дней подписки' in prizes.letter(
        {'amount': 30, 'kind': 'days'}, '')
