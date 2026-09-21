"""Под безлимитный ByPass: сколько покупает каждый и по какой цене.

Цену безлимита нельзя поставить по среднему: решение принимает не средний
человек, а каждый за себя. Поэтому проверяется распределение, привычка
докупать и модель перехода — кто перейдёт на новую цену, а кто останется
на пакетах.
"""

from datetime import timedelta

import pytest

from app.admin import bypass_report as admin
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import bypass_plan
from app.services.bypass_usage import GB

SQUAD = 'bypass-squad'
END = now()
START = END - timedelta(days=30)


class Panel:
    def __init__(self, usage=None):
        self.usage = usage or {}

    async def squad_usage(self, squad, start, end, **kwargs):
        return dict(self.usage)


@pytest.fixture
def repos(db):
    return UsersRepository(db['users']), BalanceLogRepository(db['balance_log'])


async def subscriber(users, user_id: int, left_gb: float = 0) -> None:
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}'},
        'vpn': {'uuid': f'u-{user_id}', 'bypass_uuid': f'b-{user_id}',
                'bypass_trafficLimitBytes': int(left_gb * GB)},
    })


async def bought(journal, user_id: int, price: int, gb: int,
                 days_ago: int = 1) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -price, 'kind': 'bypass',
        'at': now() - timedelta(days=days_ago), 'meta': {'gb': gb},
        'description': f'ByPass: {gb} Гб'})


async def paid_subscription(journal, user_id: int, price: int,
                            kind: str = 'plan', days_ago: int = 5) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -price, 'kind': kind,
        'at': now() - timedelta(days=days_ago), 'meta': {},
        'description': 'Покупка подписки'})


async def collect(repos, panel=None):
    users, journal = repos
    return await bypass_plan.collect(users, journal, panel or Panel(), SQUAD,
                                     START, END)


# ── строка на человека ──────────────────────────────────────────────────────
async def test_a_person_gets_his_own_row(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 90, 15)

    row = (await collect(repos))['rows'][0]

    assert row['user_id'] == 10 and row['gb_month'] == 15 and row['spent_month'] == 90


async def test_someone_who_never_bought_is_still_in_the_table(repos, db):
    """Подключивших и не купивших надо видеть: это те, кому безлимит,
    возможно, и продастся."""
    users, journal = repos
    await subscriber(users, 10)

    data = await collect(repos)

    assert len(data['rows']) == 1 and data['buyers'] == []


async def test_the_main_subscription_is_counted_apart_from_traffic(repos, db):
    """Кому безлимит продаётся вдобавок к подписке, а кому вместо неё."""
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 90, 15)
    await paid_subscription(journal, 10, 150)
    await paid_subscription(journal, 10, 150, kind='renewal')

    row = (await collect(repos))['rows'][0]

    assert row['spent_month'] == 90 and row['sub_month'] == 300


async def test_extra_devices_are_not_a_subscription_payment(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await journal.col.insert_one({
        'user_id': 10, 'amount': -75, 'kind': 'devices', 'at': now(),
        'meta': {}, 'description': 'Доп. устройство'})

    assert (await collect(repos))['rows'][0]['sub_month'] == 0


async def test_the_real_traffic_comes_from_the_panel(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5)

    row = (await collect(repos, Panel({'10': 50 * GB})))['rows'][0]

    assert row['gb_month'] == 5 and row['used_gb_month'] == 50.0


# ── привычка докупать ───────────────────────────────────────────────────────
async def test_the_gap_between_purchases_is_measured(repos, db):
    """Привычка докупать — это то, что безлимит и заменяет."""
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5, days_ago=28)
    await bought(journal, 10, 50, 5, days_ago=18)
    await bought(journal, 10, 50, 5, days_ago=8)

    row = (await collect(repos))['rows'][0]

    assert row['purchases'] == 3 and row['gap_days'] == 10.0


async def test_a_single_purchase_gives_no_interval(repos, db):
    """Ноль здесь означал бы «покупает каждый день», а это «не знаем»."""
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5)

    assert (await collect(repos))['rows'][0]['gap_days'] == 0.0


async def test_everything_is_scaled_to_a_month(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5, days_ago=2)

    users_, journal_ = repos
    data = await bypass_plan.collect(users_, journal_, Panel(), SQUAD,
                                     END - timedelta(days=15), END)

    assert data['rows'][0]['gb_month'] == 10.0 and data['rows'][0]['spent_month'] == 100


# ── распределение ───────────────────────────────────────────────────────────
async def test_people_are_split_into_buckets_by_volume(repos, db):
    users, journal = repos
    for user_id, gb in ((10, 2), (11, 20), (12, 100)):
        await subscriber(users, user_id)
        await bought(journal, user_id, gb * 10, gb)

    found = {(row['from'], row['to']): row['people']
             for row in bypass_plan.buckets((await collect(repos))['buyers'])}

    assert found[(1, 5)] == 1 and found[(15, 30)] == 1 and found[(60, 0)] == 1


def test_the_percentile_is_the_value_not_the_average():
    """Девяносто пятый на десяти значениях — это десятое, а не среднее."""
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]

    assert bypass_plan.percentile(values, 50) == 50
    assert bypass_plan.percentile(values, 95) == 1000


def test_an_empty_list_has_no_percentile():
    assert bypass_plan.percentile([], 50) == 0.0


# ── модель цены ─────────────────────────────────────────────────────────────
def person(spent: int, used: float = 0.0) -> dict:
    return {'spent_month': spent, 'used_gb_month': used, 'purchases': 1}


def test_only_those_who_pay_more_than_the_price_switch():
    rows = [person(100), person(400), person(900)]

    cheap, dear = bypass_plan.simulate(rows, [300, 1000])

    assert cheap['switchers'] == 2 and dear['switchers'] == 0


def test_those_who_stay_keep_paying_what_they_paid():
    """Иначе модель обещала бы выручку с тех, кто ничего не менял."""
    rows = [person(100), person(900)]

    result = bypass_plan.simulate(rows, [500])[0]

    assert result['revenue'] == 100 + 500


def test_a_price_above_everyone_changes_nothing():
    rows = [person(100), person(200)]

    result = bypass_plan.simulate(rows, [1000])[0]

    assert result['delta'] == 0 and result['switchers'] == 0


def test_a_cheap_price_costs_us_money():
    """Главное, ради чего модель и нужна: дешёвый безлимит забирает выручку
    у самых прибыльных."""
    rows = [person(900), person(800)]

    result = bypass_plan.simulate(rows, [300])[0]

    assert result['delta'] == 300 * 2 - 1700


def test_the_traffic_of_switchers_is_counted_with_growth():
    """Счётчик перестаёт мешать — расход растёт, и это расход, а не выручка."""
    rows = [person(900, used=50.0)]

    result = bypass_plan.simulate(rows, [300], cost_per_gb=2.0, growth=2.0)[0]

    assert result['traffic_gb'] == 100.0 and result['cost'] == 200
    assert result['profit'] == 300 - 200


def test_without_a_cost_per_gigabyte_only_revenue_is_shown():
    result = bypass_plan.simulate([person(900, used=50.0)], [300])[0]

    assert result['cost'] == 0 and result['profit'] == 300


# ── экран и выгрузка ────────────────────────────────────────────────────────
async def test_the_report_says_when_nobody_buys(repos, db):
    users, journal = repos
    await subscriber(users, 10)

    text = admin.render_plan(await collect(repos), 0.0)

    assert 'цену безлимита считать не из чего' in text


async def test_the_report_warns_that_the_cost_is_unknown(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 90, 15)

    text = admin.render_plan(await collect(repos), 0.0)

    assert 'Себестоимость гигабайта' in text


async def test_the_report_shows_the_price_table(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 900, 100)

    text = admin.render_plan(await collect(repos), 2.0)

    assert 'Что будет при безлимите' in text
    assert '700₽</b> → перейдут 1' in text


async def test_the_csv_has_a_line_per_person(repos, db):
    users, journal = repos
    await subscriber(users, 10, left_gb=3)
    await subscriber(users, 11)
    await bought(journal, 10, 90, 15)

    text = admin.plan_csv(await collect(repos)).decode('utf-8-sig')
    lines = [line for line in text.splitlines() if line]

    assert lines[0].startswith('id;username')
    assert len(lines) == 3
