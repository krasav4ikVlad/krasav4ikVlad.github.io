"""Билеты розыгрыша: за что они даются и за что нет.

Розыгрыш нельзя пересчитать после того, как приз уехал, поэтому правило
проверяется целиком. Источников билета два — новый приглашённый друг и
своя покупка, — и главное здесь, что ни один нельзя получить бесплатно:
билет даёт только купленная подписка, а не деньги на балансе.
"""

from datetime import timedelta

import pytest

from app.admin import raffle as admin
from app.services import excel
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


async def test_the_table_has_a_line_per_ticket(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=3)

    rows = admin.ticket_rows(await collect(repos))

    assert len(rows) == 3
    assert [row[0] for row in rows] == [1, 2, 3]


async def test_the_user_table_has_a_line_per_person(repos, db):
    """В файле билетов человек с тремя билетами занимает три строки, и
    «сколько всего участников» по нему не посчитать."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 1, months=3)
    await bought(journal, 20)

    rows = admin.user_rows(await collect(repos))

    assert len(rows) == 2
    owner = next(row for row in rows if row[0] == 1)
    assert owner[2] == 6 and owner[3] == 3 and owner[4] == 1 and owner[5] == 3


async def test_the_table_opens_in_excel(repos, db):
    journal, users = repos
    await person(users, 1)
    await bought(journal, 1, months=1)

    body, ext = excel.build(admin.TICKET_COLUMNS,
                            admin.ticket_rows(await collect(repos)), 'Билеты')

    assert ext == 'xlsx' and body.startswith(b'PK')


async def test_the_file_is_named_after_the_period(repos, db):
    name = admin.filename(await collect(repos), 'raffle-tickets', 'xlsx')

    assert name == 'raffle-tickets-01.10.2026-22.10.2026.xlsx'


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


# ── призы и жребий ──────────────────────────────────────────────────────────
#
# Жребий внутри бота проверяемым не сделать кодом: снаружи видно только
# результат. Проверяемым его делает порядок — список билетов публикуется
# до броска, — поэтому здесь важно, что бросок один и что он сохраняется.

def test_prizes_are_unrolled_one_per_winner():
    prizes = domain.parse_prizes('iPhone 18 Pro\nAirPods 5\n5000₽ x10\n'
                                 'Подписка на месяц x25')

    assert len(prizes) == 37
    assert prizes[0] == 'iPhone 18 Pro' and prizes[-1] == 'Подписка на месяц'


def test_a_prize_without_a_count_is_one_prize():
    assert domain.parse_prizes('\nPlayStation 5\n\n') == ['PlayStation 5']


def test_a_price_in_the_name_is_not_a_count():
    """«5000₽» — это название приза, а не «5000 штук»."""
    assert domain.parse_prizes('5000₽') == ['5000₽']


class Rng:
    """Предсказуемый жребий: тащим всегда первый билет из оставшихся."""

    @staticmethod
    def randrange(size: int) -> int:
        return 0


def tickets_of(owner: int, count: int, start: int = 1) -> list[dict]:
    return [{'ticket': start + index, 'owner': owner,
             'owner_username': f'u{owner}'} for index in range(count)]


def test_one_person_wins_only_once():
    """Человек с сотней билетов иначе забрал бы половину призов — и это
    выглядело бы подтасовкой, чем бы оно ни было."""
    pool = tickets_of(1, 50) + tickets_of(2, 1, start=51)

    winners = domain.draw(pool, 5, Rng())

    assert [row['owner'] for row in winners] == [1, 2]


def test_the_draw_stops_when_people_run_out():
    assert domain.draw(tickets_of(1, 3), 10, Rng()) == tickets_of(1, 3)[:1]


def test_nothing_is_drawn_from_an_empty_pool():
    assert domain.draw([], 5, Rng()) == [] and domain.draw(tickets_of(1, 1), 0) == []


def test_every_ticket_can_win():
    """Тащим билет, а не участника: у кого билетов больше, у того и шанс
    выше. Если бы тащили участника, билеты не значили бы ничего."""
    pool = tickets_of(1, 1) + tickets_of(2, 1, start=2)
    seen = {domain.draw(pool, 1)[0]['owner'] for _ in range(100)}

    assert seen == {1, 2}


async def setup_draw(container, people: int = 3) -> None:
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')
    for user_id in range(1, people + 1):
        await container.users.create({
            'user_data': {'user_id': user_id, 'username': f'u{user_id}'},
            'vpn': {'uuid': f'u-{user_id}', 'expireAt': now() + timedelta(days=10)}})
        await container.db['balance_log'].insert_one({
            'user_id': user_id, 'amount': -150, 'kind': 'plan',
            'at': START.replace(day=4), 'meta': {'days': 30},
            'description': 'Покупка подписки'})


async def test_the_draw_names_a_winner_for_every_prize(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container)
    await container.settings.set('raffle.prizes', 'iPhone 18 Pro\nAirPods 5')

    await dp.feed_update(bot, message('/raffledraw'))

    assert 'iPhone 18 Pro' in session.last_text and 'AirPods 5' in session.last_text
    assert 'билет №' in session.last_text


async def test_the_draw_is_not_thrown_twice(admin_env):
    """Второй бросок, из которого выбирают понравившийся, — уже не
    розыгрыш. Повтор показывает тот же результат."""
    dp, bot, session, container = admin_env
    await setup_draw(container, people=20)
    await container.settings.set('raffle.prizes', 'iPhone 18 Pro')

    await dp.feed_update(bot, message('/raffledraw'))
    first = session.last_text
    await dp.feed_update(bot, message('/raffledraw'))

    assert session.last_text == first


async def test_the_draw_can_be_thrown_again_on_purpose(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container)
    await container.settings.set('raffle.prizes', 'iPhone 18 Pro')

    await dp.feed_update(bot, message('/raffledraw'))
    saved = await container.db['raffle_draws'].find_one({})
    await dp.feed_update(bot, message('/raffledraw заново'))
    again = await container.db['raffle_draws'].find_one({})

    assert again['at'] != saved['at']


async def test_the_draw_writes_only_to_the_admin(admin_env):
    """Победителям бот не пишет: поздравление — это разговор, и его ведёт
    человек."""
    dp, bot, session, container = admin_env
    await setup_draw(container)
    await container.settings.set('raffle.prizes', 'iPhone 18 Pro\nAirPods 5')

    await dp.feed_update(bot, message('/raffledraw'))

    assert len([name for name, _ in session.calls if name == 'SendMessage']) <= 2


async def test_the_draw_asks_for_prizes_when_there_are_none(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container)
    await container.settings.set('raffle.prizes', '')

    await dp.feed_update(bot, message('/raffledraw'))

    assert 'списка призов' in session.last_text


async def test_the_participants_file_is_sent(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container)

    await dp.feed_update(bot, message('/raffleusers'))

    assert [name for name, _ in session.calls if name == 'SendDocument']


async def test_prizes_are_credited_without_writing_to_the_winner(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container, people=1)

    await dp.feed_update(bot, message('/rafflewin 1 5000'))

    assert (await container.users.get(1))['info']['balance'] == 5000
    # два сообщения админу — «выдаю» и отчёт; победителю ни одного
    assert len([name for name, _ in session.calls if name == 'SendMessage']) == 2


# ── статистика билетов ──────────────────────────────────────────────────────
#
# Сводка отвечает «сколько всего», статистика — «как идёт и у кого». Тысяча
# билетов у пятнадцати человек и та же тысяча у четырёхсот — разные акции
# с одинаковой суммой, и различить их должно быть чем.

def ticket(owner: int, day: int, kind: str = domain.SELF) -> dict:
    return {'ticket': owner * 100 + day, 'owner': owner, 'kind': kind,
            'at': START.replace(day=day)}


def who(user_id: int, tickets: int, friends: int = 0) -> dict:
    return {'user_id': user_id, 'tickets': tickets, 'friends': friends}


def test_the_median_is_shown_next_to_the_average():
    """Среднее врёт: один человек с сотней билетов поднимает его всем."""
    rows = [ticket(1, 5) for _ in range(100)] + [ticket(index, 5)
                                                 for index in range(2, 12)]
    people = [who(1, 100)] + [who(index, 1) for index in range(2, 12)]

    numbers = domain.stats(rows, people)

    assert numbers['average'] == 10.0 and numbers['median'] == 1
    assert numbers['most'] == 100


def test_tickets_are_split_by_day():
    numbers = domain.stats([ticket(1, 5), ticket(2, 5), ticket(3, 6)],
                           [who(1, 1), who(2, 1), who(3, 1)])

    assert [row['tickets'] for row in numbers['days']] == [2, 1]
    assert [row['day'] for row in numbers['days']] == ['05.10', '06.10']


def test_a_person_is_counted_new_on_their_first_day_only():
    """Иначе «сколько человек пришло за день» превращается в «сколько
    было активно», а это другой вопрос."""
    numbers = domain.stats([ticket(1, 5), ticket(1, 6), ticket(2, 6)],
                           [who(1, 2), who(2, 1)])

    assert [row['people'] for row in numbers['days']] == [1, 1]


def test_people_are_split_into_buckets():
    numbers = domain.stats([ticket(1, 5)],
                           [who(1, 1), who(2, 2), who(3, 5), who(4, 40)])

    assert [row['people'] for row in numbers['buckets']] == [1, 1, 1, 1]


def test_those_who_actually_invited_are_counted():
    """Главное число акции «приведи друга»: остальные просто продлились."""
    numbers = domain.stats([ticket(1, 5, domain.FRIEND), ticket(2, 5)],
                           [who(1, 3, friends=1), who(2, 1)])

    assert numbers['inviters'] == 1
    assert numbers['friends'] == 1 and numbers['own'] == 1


def test_empty_stats_do_not_divide_by_zero():
    numbers = domain.stats([], [])

    assert numbers['total'] == 0 and numbers['average'] == 0.0


def test_the_bar_is_proportional_and_never_empty_for_a_nonzero_day():
    """День с одним билетом на фоне двухсот всё равно должен быть виден."""
    assert len(admin.bar(200, 200)) == admin.BAR_WIDTH
    assert admin.bar(1, 200) != ''
    assert admin.bar(0, 200) == ''


async def test_the_stats_command_answers_with_numbers(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container, people=3)

    await dp.feed_update(bot, message('/rafflestats'))

    assert 'Статистика билетов' in session.last_text
    assert 'По дням' in session.last_text and 'Сколько у кого' in session.last_text


async def test_the_stats_command_does_not_break_on_an_empty_contest(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('raffle.start', '01.10.2026')
    await container.settings.set('raffle.end', '22.10.2026')

    await dp.feed_update(bot, message('/rafflestats'))

    assert 'ни одного' in session.last_text


# ── «новый» друг, когда журнал моложе бота ──────────────────────────────────
#
# Журнал (balance_log) завели позже, чем запустили бота: у человека,
# пришедшего два года назад, первой покупки в нём нет вовсе. Проверять
# новизну по одному журналу значит считать новыми всех старых — а это ровно
# те, за кого билетов давать нельзя.

async def old_customer(users, user_id: int, referrer: int, *, when) -> None:
    """Друг, который покупал давно — так, что в журнал это не попало."""
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'referrer': referrer},
        'info': {'balance': 0, 'transactions': [
            {'amount': -150, 'dt': when, 'description': 'Покупка подписки «1 месяц»'}]},
        'vpn': {'uuid': f'u-{user_id}', 'expireAt': now() + timedelta(days=30)},
    })


def test_an_old_purchase_is_seen_in_the_card():
    assert domain.bought_before(
        [{'amount': -150, 'dt': START - timedelta(days=200),
          'description': 'Покупка подписки «1 месяц»'}], START) is True


def test_the_old_format_of_transactions_is_understood():
    """В базе лежат и списки, и словари — оба формата настоящие."""
    assert domain.bought_before(
        [[-150, (START - timedelta(days=200)).strftime('%d.%m.%Y %H:%M:%S'),
          'Продление подписки «1 месяц»']], START) is True


def test_a_topup_is_not_a_purchase():
    assert domain.bought_before(
        [{'amount': 500, 'dt': START - timedelta(days=200),
          'description': 'Пополнение (wata), бонус 100₽'}], START) is False


def test_a_refund_is_not_a_purchase():
    """«Возврат: покупка не удалась» — это плюс на баланс, а не покупка."""
    assert domain.bought_before(
        [{'amount': 150, 'dt': START - timedelta(days=200),
          'description': 'Возврат: покупка подписки не удалась'}], START) is False


def test_a_purchase_inside_the_contest_does_not_make_him_old():
    assert domain.bought_before(
        [{'amount': -150, 'dt': START + timedelta(days=1),
          'description': 'Покупка подписки «1 месяц»'}], START) is False


async def test_an_old_customer_gives_no_tickets_even_if_the_journal_is_young(repos, db):
    """Главная проверка: в журнале его прошлых покупок нет, и по журналу он
    выглядит новым. Карточка помнит дольше."""
    journal, users = repos
    await person(users, 1)
    await old_customer(users, 20, referrer=1, when=START - timedelta(days=200))
    await bought(journal, 20, day=5)

    data = await collect(repos)

    assert data['skipped'].get(domain.WAS_CUSTOMER) == 1
    # приглашающему — ничего; сам друг свой билет за подписку получает
    assert not [row for row in data['participants'] if row['user_id'] == 1]


async def test_a_truly_new_friend_still_counts(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, day=5)

    data = await collect(repos)
    owner = next(row for row in data['participants'] if row['user_id'] == 1)
    assert owner['tickets'] == 3


async def test_the_screen_hides_the_old_customer_too(repos, db):
    """Экран и таблица обязаны считать одно и то же — иначе человек увидит
    билет, которого в списке не будет."""
    journal, users = repos
    await person(users, 1)
    await old_customer(users, 20, referrer=1, when=START - timedelta(days=200))
    await bought(journal, 20, day=5)

    mine = await raffle.for_user(journal, users, 1, start=START, end=END)

    assert mine['tickets'] == 0 and mine['friends'] == 0


async def test_the_report_says_how_deep_the_journal_goes(admin_env):
    """«Точно ли только новые?» — вопрос, на который отчёт обязан отвечать
    сам, а не заставлять верить на слово."""
    dp, bot, session, container = admin_env
    await setup_draw(container, people=2)

    await dp.feed_update(bot, message('/raffle'))

    assert 'Журнал покупок ведётся с' in session.last_text


# ── три билета ровно за месяц ───────────────────────────────────────────────
#
# Правило объявлено в канале: три билета даёт новый друг, купивший подписку
# от месяца. Ни неделя, ни пополнение баланса билета не дают, а полгода
# дают те же три — билеты считаются за человека, а не за срок.

async def tickets_of_owner(repos, user_id: int = 1) -> int:
    data = await collect(repos)
    row = next((item for item in data['participants']
                if item['user_id'] == user_id), None)
    return row['tickets'] if row else 0


async def test_a_friend_with_a_week_gives_nothing(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await journal.col.insert_one({
        'user_id': 20, 'amount': -75, 'kind': 'plan',
        'at': START.replace(day=5), 'meta': {'days': 7},
        'description': 'Покупка подписки «неделя»'})

    assert await tickets_of_owner(repos) == 0


async def test_a_friend_with_exactly_one_month_gives_three(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1)

    assert await tickets_of_owner(repos) == 3


async def test_a_friend_with_half_a_year_gives_the_same_three(repos, db):
    """Билеты за друга — за человека, а не за срок: иначе один друг с
    годовой подпиской перевесил бы четверых приведённых."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=6)

    assert await tickets_of_owner(repos) == 3


async def test_two_new_friends_give_six(repos, db):
    journal, users = repos
    await person(users, 1)
    for friend_id in (20, 21):
        await person(users, friend_id, referrer=1)
        await bought(journal, friend_id, months=1)

    assert await tickets_of_owner(repos) == 6


async def test_a_friend_who_only_topped_up_gives_nothing(repos, db):
    """Деньги на балансе — ещё не подписка."""
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await topped_up(journal, 20, amount=5000)

    assert await tickets_of_owner(repos) == 0


# ── кого не учитывать ───────────────────────────────────────────────────────
#
# Свои и рабочие аккаунты покупают по-настоящему, и билеты им по правилам
# полагаются. Но приз, ушедший внутрь, обесценивает всю акцию, а доказать
# потом, что так и было задумано, нечем.

def test_ids_are_read_in_any_form():
    assert domain.parse_ids('7996131040') == frozenset({7996131040})
    assert domain.parse_ids('123, 456\n789') == frozenset({123, 456, 789})
    assert domain.parse_ids('') == frozenset()
    assert domain.parse_ids('не число') == frozenset()


async def test_an_excluded_person_gets_no_tickets_of_their_own(repos, db):
    journal, users = repos
    await person(users, 7996131040)
    await bought(journal, 7996131040, months=6)

    data = await collect(repos, exclude='7996131040')

    assert data['tickets'] == 0
    assert data['skipped'].get(domain.EXCLUDED) == 1


async def test_an_excluded_person_brings_no_tickets_to_whoever_invited_them(repos, db):
    """Иначе его вывели бы из розыгрыша только наполовину."""
    journal, users = repos
    await person(users, 1)
    await person(users, 7996131040, referrer=1)
    await bought(journal, 7996131040, months=1)

    data = await collect(repos, exclude='7996131040')

    assert not [row for row in data['participants'] if row['user_id'] == 1]


async def test_an_excluded_inviter_gets_nothing_for_real_friends(repos, db):
    journal, users = repos
    await person(users, 7996131040)
    await person(users, 20, referrer=7996131040)
    await bought(journal, 20, months=1)

    data = await collect(repos, exclude='7996131040')

    assert not [row for row in data['participants']
                if row['user_id'] == 7996131040]


async def test_everyone_else_is_untouched(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 20, referrer=1)
    await bought(journal, 20, months=1)

    data = await collect(repos, exclude='7996131040')
    owner = next(row for row in data['participants'] if row['user_id'] == 1)

    assert owner['tickets'] == 3


async def test_the_excluded_screen_shows_zero(repos, db):
    """Экран и таблица обязаны совпадать и здесь: иначе человек увидит
    билеты, которых в списке нет."""
    journal, users = repos
    await person(users, 7996131040)
    await bought(journal, 7996131040, months=6)

    mine = await raffle.for_user(journal, users, 7996131040, start=START,
                                 end=END, exclude='7996131040')

    assert mine['tickets'] == 0


async def test_an_excluded_friend_is_not_counted_on_the_screen(repos, db):
    journal, users = repos
    await person(users, 1)
    await person(users, 7996131040, referrer=1)
    await bought(journal, 7996131040, months=1)

    mine = await raffle.for_user(journal, users, 1, start=START, end=END,
                                 exclude='7996131040')

    assert mine['tickets'] == 0 and mine['friends'] == 0


async def test_the_report_names_who_is_excluded(admin_env):
    """Молча выкинутый из розыгрыша участник — это то, что потом не
    объяснишь. В сводке видно, кого не считают."""
    dp, bot, session, container = admin_env
    await setup_draw(container, people=2)
    await container.settings.set('raffle.exclude', '7996131040')

    await dp.feed_update(bot, message('/raffle'))

    assert '7996131040' in session.last_text


# ── кто выиграл по номеру ───────────────────────────────────────────────────
#
# Числа тянет генератор на видео, а не бот: зритель видит и опубликованный
# список билетов, и сам бросок. Боту остаётся ответить, чей это номер, и не
# дать одному человеку забрать два приза.

def test_numbers_are_read_in_any_form():
    assert domain.parse_numbers('1423, 77\n2890') == [1423, 77, 2890]
    assert domain.parse_numbers('1 два 3') == [1, 3]
    assert domain.parse_numbers('') == []


def test_the_order_of_numbers_is_kept():
    """Первый номер — первый приз, и путать их нельзя."""
    assert domain.parse_numbers('9 1 5') == [9, 1, 5]


def ticket_row(number: int, owner: int) -> dict:
    return {'ticket': number, 'owner': owner, 'owner_username': f'u{owner}',
            'kind': domain.SELF, 'detail': '', 'at': START}


def test_a_ticket_finds_its_owner():
    found = domain.lookup([ticket_row(1, 10), ticket_row(2, 20)], [2])

    assert found[0]['row']['owner'] == 20 and found[0]['place'] == 1


def test_a_number_that_does_not_exist_is_reported():
    """Молча пропустить номер нельзя: на видео его уже назвали."""
    found = domain.lookup([ticket_row(1, 10)], [99])

    assert found[0]['row'] is None and found[0]['ticket'] == 99


def test_a_second_win_by_the_same_person_is_flagged():
    """Один приз в одни руки — о повторе нужно сказать сразу, чтобы
    вытянуть замену, не сходя с записи."""
    rows = [ticket_row(1, 10), ticket_row(2, 10), ticket_row(3, 20)]

    found = domain.lookup(rows, [1, 3, 2])

    assert [item['repeat'] for item in found] == [0, 0, 1]


def test_too_many_numbers_are_cut():
    assert len(domain.parse_numbers(' '.join(str(n) for n in range(1, 300)))) \
        == domain.MAX_LOOKUP


async def test_the_command_tells_the_range_without_arguments(admin_env):
    """Верхняя граница для генератора — это и есть число билетов."""
    dp, bot, session, container = admin_env
    await setup_draw(container, people=3)

    await dp.feed_update(bot, message('/rafflewho'))

    assert 'Билетов сейчас' in session.last_text
    assert 'от 1 до 3' in session.last_text


async def test_the_command_names_the_owner(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container, people=3)

    await dp.feed_update(bot, message('/rafflewho 1 2'))

    assert '№1' in session.last_text and '№2' in session.last_text


async def test_the_command_says_when_the_number_is_out_of_range(admin_env):
    dp, bot, session, container = admin_env
    await setup_draw(container, people=2)

    await dp.feed_update(bot, message('/rafflewho 999'))

    assert 'такого билета нет' in session.last_text
