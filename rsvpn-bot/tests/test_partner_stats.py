"""Статистика партнёра по дням.

Вопрос про партнёра всегда один и тот же: «как у него идёт». Сумма за всё
время на него не отвечает — по ней не видно ни всплеска после ролика, ни
того, что ссылка неделю назад перестала работать. Отвечают дни подряд, и
проверяется здесь именно это: что день считается днём, пустой день виден
пустым, а деньги ложатся на день оплаты, а не на день регистрации.
"""

from datetime import timedelta

import pytest

from app.admin import partners
from app.bot.callbacks import Admin as Adm
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import partner_stats

from tests.test_admin_panel import ADMIN, admin_env, callback  # noqa: F401

TAG = 'vlad'


@pytest.fixture
def repos(db):
    return UsersRepository(db['users']), BalanceLogRepository(db['balance_log'])


async def came(users, user_id: int, *, tag: str = TAG, days_ago: int = 0) -> None:
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'ref_tag': tag,
                      'date_joined': now() - timedelta(days=days_ago)},
        'info': {'balance': 0}})


async def topped_up(journal, user_id: int, paid: int, *, bonus: int = 0,
                    days_ago: int = 0) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': paid + bonus, 'kind': 'topup',
        'at': now() - timedelta(days=days_ago),
        'meta': {'paid': paid, 'bonus': bonus, 'provider': 'wata'}})


def day_of(data: dict, days_ago: int) -> dict:
    key = (now() - timedelta(days=days_ago)).strftime('%d.%m')
    return next(row for row in data['days'] if row['day'] == key)


async def look(repos, days: int = 14) -> dict:
    users, journal = repos
    return await partner_stats.by_day(users, journal, tag=TAG, days=days)


# ── люди по дням ────────────────────────────────────────────────────────────
async def test_today_shows_today(repos, db):
    users, _ = repos
    await came(users, 1)
    await came(users, 2)

    assert (await look(repos))['today']['people'] == 2


async def test_each_day_keeps_its_own_people(repos, db):
    """Иначе вчерашний всплеск незаметен: всё сливается в одно число."""
    users, _ = repos
    await came(users, 1, days_ago=1)
    await came(users, 2, days_ago=1)
    await came(users, 3, days_ago=3)

    data = await look(repos)

    assert day_of(data, 1)['people'] == 2
    assert day_of(data, 3)['people'] == 1


async def test_a_quiet_day_is_shown_as_zero(repos, db):
    """Пропущенный день — это не «ничего», это «ссылка не работает»."""
    users, _ = repos
    await came(users, 1, days_ago=2)

    data = await look(repos)

    assert len(data['days']) == 14
    assert day_of(data, 1)['people'] == 0


async def test_someone_elses_tag_is_not_counted(repos, db):
    users, _ = repos
    await came(users, 1)
    await came(users, 2, tag='othertag')

    assert (await look(repos))['people'] == 1


async def test_people_from_before_the_window_are_still_in_the_total(repos, db):
    """За период — ноль, а всего по метке — человек: это разные числа, и
    обоих ждут на экране."""
    users, _ = repos
    await came(users, 1, days_ago=100)

    data = await look(repos)

    assert data['people'] == 0 and data['total_people'] == 1


# ── деньги по дням ──────────────────────────────────────────────────────────
async def test_the_money_lands_on_the_day_it_was_paid(repos, db):
    users, journal = repos
    await came(users, 1, days_ago=5)
    await topped_up(journal, 1, paid=500, days_ago=1)

    data = await look(repos)

    assert day_of(data, 1)['paid'] == 500
    assert day_of(data, 5)['paid'] == 0


async def test_an_old_referral_paying_today_is_todays_money(repos, db):
    """Человек из августа платит сегодня — и это сегодняшние деньги
    партнёра, хотя в сегодняшних регистрациях его нет."""
    users, journal = repos
    await came(users, 1, days_ago=100)
    await topped_up(journal, 1, paid=700)

    data = await look(repos)

    assert data['today']['paid'] == 700 and data['today']['people'] == 0


async def test_the_bonus_is_not_counted_as_his_money(repos, db):
    """Бонус за пополнение — наш подарок, к работе партнёра отношения
    не имеет."""
    users, journal = repos
    await came(users, 1)
    await topped_up(journal, 1, paid=1000, bonus=300)

    assert (await look(repos))['today']['paid'] == 1000


async def test_someone_elses_payment_does_not_get_in(repos, db):
    users, journal = repos
    await came(users, 1, tag='othertag')
    await topped_up(journal, 1, paid=1000)

    assert (await look(repos))['paid'] == 0


async def test_payments_older_than_the_window_are_left_out(repos, db):
    users, journal = repos
    await came(users, 1, days_ago=100)
    await topped_up(journal, 1, paid=1000, days_ago=90)

    assert (await look(repos))['paid'] == 0


# ── полоска ─────────────────────────────────────────────────────────────────
#
# Столбик читается взглядом, а колонка чисел — нет: ради этого он и нужен.

def test_the_busiest_day_gets_a_full_bar():
    assert partner_stats.bar(10, 10) == '█' * partner_stats.BAR_WIDTH


def test_a_small_day_still_gets_something_to_see():
    """Округление вниз стёрло бы день с одним человеком в пустоту, а он
    отличается от нуля."""
    assert partner_stats.bar(1, 100) == '█'


def test_an_empty_day_has_no_bar():
    assert partner_stats.bar(0, 10) == ''


def test_half_the_best_day_is_half_a_bar():
    assert len(partner_stats.bar(5, 10)) == partner_stats.BAR_WIDTH // 2


# ── экран ───────────────────────────────────────────────────────────────────
async def test_the_button_opens_the_daily_stats(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create(TAG, ADMIN.id)
    await came(c.users, 1)

    await dp.feed_update(bot, callback(Adm(act='pstat', a=TAG).pack()))

    assert 'по дням' in session.last_text
    assert now().strftime('%d.%m') in session.last_text


async def test_the_screen_shows_the_numbers_of_the_day(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create(TAG, ADMIN.id)
    await came(c.users, 1)
    await topped_up(c.balance_log, 1, paid=450)

    await dp.feed_update(bot, callback(Adm(act='pstat', a=TAG).pack()))

    assert '450₽' in session.last_text


async def test_a_longer_period_can_be_asked_for(admin_env):
    """Две недели отвечают на «как сейчас», месяц — на «как вообще»."""
    dp, bot, session, c = admin_env
    await c.ref_tags.create(TAG, ADMIN.id)
    await came(c.users, 1, days_ago=20)

    await dp.feed_update(bot, callback(Adm(act='pstat', a=TAG).pack()))
    short = session.last_text
    await dp.feed_update(bot, callback(Adm(act='pstat', a=TAG, b='long').pack()))

    assert 'За 14 дн. пришло: <b>0</b>' in short
    assert 'За 30 дн. пришло: <b>1</b>' in session.last_text


async def test_the_stats_button_is_on_the_partner_card(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create(TAG, ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pshow', a=TAG).pack()))

    assert 'pstat' in str(session.markups[-1])


async def test_an_unknown_tag_does_not_open_a_screen(admin_env):
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, callback(Adm(act='pstat', a='nosuch').pack()))

    assert 'по дням' not in session.last_text
