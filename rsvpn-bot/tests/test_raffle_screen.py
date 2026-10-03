"""Экран розыгрыша у человека и выдача призов.

Порог, которого человек не видит, работает вполсилы — поэтому главное
здесь, что билеты на экране считаются по тем же правилам, что и таблица
для розыгрыша. Разойдись они, и правым окажется человек, а не бот.
"""

from datetime import timedelta

import pytest

from app.bot.handlers import raffle as screen
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import raffle
from app.services import raffle_prizes as prizes
from app.settings.service import SettingsService

START = now() - timedelta(days=3)
END = now() + timedelta(days=4)


@pytest.fixture
def repos(db):
    return BalanceLogRepository(db['balance_log']), UsersRepository(db['users'])


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


async def bought(journal, user_id: int, at, *, months: int = 1,
                 kind: str = 'plan') -> None:
    """Покупка подписки — единственное, что даёт билет."""
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -150 * months, 'kind': kind, 'at': at,
        'meta': {'days': months * 30}, 'description': 'Покупка подписки'})


async def mine(repos, user_id: int = 1, **kwargs):
    journal, users = repos
    return await raffle.for_user(journal, users, user_id, start=START,
                                 end=END, **kwargs)


# ── билеты одного человека ──────────────────────────────────────────────────
async def test_a_new_friend_gives_me_three_tickets(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 3


async def test_my_own_subscription_gives_a_ticket_per_month(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, now() - timedelta(days=1), months=6)

    row = await mine(repos)

    assert row['tickets'] == 6 and row['own_months'] == 6


async def test_a_friend_who_bought_before_the_contest_does_not(repos, db):
    """То же правило, что и в таблице: друг должен быть новым."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, START - timedelta(days=10))
    await bought(journal, 20, now() - timedelta(days=1), kind='renewal')

    assert (await mine(repos))['tickets'] == 0


async def test_a_friend_of_someone_else_gives_me_nothing(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=999)
    await bought(journal, 20, now() - timedelta(days=1))

    assert (await mine(repos))['tickets'] == 0


async def test_a_friend_whose_subscription_died_does_not(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1, alive=False)
    await bought(journal, 20, now() - timedelta(days=1))

    assert (await mine(repos, require_active=True))['tickets'] == 0


async def test_inviting_yourself_gives_only_my_own_tickets(repos, db):
    """Себя привести нельзя, но свою же подписку никто не отнимает."""
    journal, users = repos
    await person(users, 1, referrer=1)
    await bought(journal, 1, now() - timedelta(days=1), months=2)

    row = await mine(repos)

    assert row['friends'] == 0 and row['tickets'] == 2


async def test_the_screen_matches_the_table(repos, db):
    """Главная проверка: экран и таблица считают одно и то же."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, now() - timedelta(days=2), months=2)
    for friend_id in (20, 21):
        await person(users, friend_id, referrer=1)
        await bought(journal, friend_id, now() - timedelta(days=1))
    await person(users, 30, referrer=1, alive=False)
    await bought(journal, 30, now() - timedelta(days=1))

    table = await raffle.collect(journal, users, start=START, end=END)
    screen_count = await mine(repos)
    owner = next(row for row in table['participants'] if row['user_id'] == 1)

    assert screen_count['tickets'] == owner['tickets'] == 2 + 3 + 3


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


# ── почему кнопки не видно ──────────────────────────────────────────────────
#
# Пустая дата выглядит снаружи как сломанная кнопка: в профиле её нет, и
# сказать об этом некому. Сводка должна отвечать на этот вопрос сама.

def test_the_summary_says_the_button_is_hidden_without_dates():
    from app.admin.raffle import button_note

    assert 'даты акции не заданы' in button_note(None, None)


def test_the_summary_says_when_the_button_will_appear():
    from app.admin.raffle import button_note

    later = now() + timedelta(days=5)
    assert 'появится' in button_note(later, later + timedelta(days=7))


def test_the_summary_confirms_the_button_is_visible():
    from app.admin.raffle import button_note

    assert 'видна всем' in button_note(START, END)


def test_the_summary_says_the_button_is_gone_after_the_contest():
    from app.admin.raffle import button_note

    old = now() - timedelta(days=30)
    assert 'убрана' in button_note(old - timedelta(days=7), old)


def test_the_hint_about_dates_is_in_the_refusal():
    """Ответ «не вижу даты» без адреса, куда их вписать, — половина ответа."""
    from app.admin.raffle import NO_DATES

    assert 'Розыгрыш' in NO_DATES and '22.09.2026' in NO_DATES


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


async def shown(repos, db, settings, user_id: int = 1) -> str:
    """Текст экрана розыгрыша так, как его увидит человек."""
    journal, users = repos
    captured = []

    class Event:
        from_user = type('U', (), {'id': user_id})()

        async def answer(self, text='', **kwargs):
            captured.append(text)
            return True

    class Container:
        def __init__(self, journal, users):
            self.balance_log = journal
            self.users = users

        def media(self, key):
            return None

    await screen.screen(Event(), Container(journal, users),
                        await users.get(user_id), settings)
    return captured[0]


async def test_the_threshold_is_counted_in_tickets_not_in_friends(repos, db,
                                                                  settings):
    """Порог стоит в билетах, а друг даёт сразу три: «осталось два друга»
    при пороге в три билета — прямая неправда, и человек приведёт лишних."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, now() - timedelta(days=1))      # 1 билет
    await settings.set('raffle.bonus_tickets', 3)
    await settings.set('raffle.bonus_days', 7)

    text = await shown(repos, db, settings)

    line = next(row for row in text.split('\n') if 'До подарка' in row)
    assert 'ещё <b>2 билета</b>' in line
    assert 'друг' not in line


async def test_by_default_no_days_are_promised_to_anyone(repos, db, settings):
    """Подарок за порог — решение, которое принимают руками. Включённый по
    умолчанию, он обещает дни сотням людей раньше, чем это кто-то заметил."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, now() - timedelta(days=1), months=6)

    text = await shown(repos, db, settings)

    assert 'До подарка' not in text and 'Подарок ваш' not in text


async def test_the_gift_is_promised_once_the_threshold_is_passed(repos, db,
                                                                 settings):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, now() - timedelta(days=1))     # 3 билета
    await settings.set('raffle.bonus_tickets', 3)
    await settings.set('raffle.bonus_days', 7)

    text = await shown(repos, db, settings)

    assert 'Подарок ваш: +7 дней' in text
    assert 'ещё 3 билета' in text          # столько даёт следующий друг


async def test_the_empty_screen_names_the_real_ticket_prices(repos, db, settings):
    """Числа на экране — из настроек: поменяли цену билета, а экран учит
    старой — и правым окажется экран."""
    journal, users = repos
    await person(users, 1)
    await settings.set('raffle.friend_tickets', 5)
    await settings.set('raffle.self_per_month', 2)
    await settings.set('raffle.bonus_tickets', 0)

    text = await shown(repos, db, settings)

    assert '5 билетов даёт новый друг' in text
    assert '2 билета — каждый месяц' in text


async def test_the_screen_says_something_even_without_a_threshold_gift(repos, db,
                                                                       settings):
    """Порог можно выключить, но экран без единой строки после числа
    выглядит недоделанным."""
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, now() - timedelta(days=1), months=2)
    await settings.set('raffle.bonus_tickets', 0)

    captured = []

    class Event:
        from_user = type('U', (), {'id': 1})()

        async def answer(self, text='', **kwargs):
            captured.append(text)
            return True

    class Container:
        def __init__(self, journal, users):
            self.balance_log = journal
            self.users = users

        def media(self, key):
            return None

    await screen.screen(Event(), Container(journal, users),
                        await users.get(1), settings)

    assert 'Ваших билетов: 2' in captured[0]
    assert 'ещё билеты' in captured[0]
