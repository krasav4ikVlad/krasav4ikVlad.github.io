"""Сколько ByPass ест и сколько приносит.

Главная ловушка здесь — среднее. Считать его по всем, кто подключил ByPass,
нельзя: подключение бесплатное, и половина людей не качает ничего. Такое
«среднее» занижает расход втрое и ведёт к неверному выводу про коэффициент.
"""

from datetime import timedelta

import pytest

from app.admin import bypass_report as admin
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import bypass_usage
from app.services.bypass_usage import GB

SQUAD = 'bypass-squad'
END = now()
START = END - timedelta(days=30)


class Panel:
    """Панель, которая отдаёт расход сквада и по id, и по username — как есть."""

    def __init__(self, usage=None, broken: bool = False):
        self.usage = usage or {}
        self.broken = broken
        self.asked: list[tuple] = []

    async def squad_usage(self, squad, start, end, **kwargs):
        if self.broken:
            raise RuntimeError('панель молчит')
        self.asked.append((squad, start, end))
        return dict(self.usage)


@pytest.fixture
def repos(db):
    return UsersRepository(db['users']), BalanceLogRepository(db['balance_log'])


async def subscriber(users, user_id: int, left_gb: float = 0) -> None:
    await users.create({
        'user_data': {'user_id': user_id},
        'vpn': {'uuid': f'u-{user_id}', 'bypass_uuid': f'b-{user_id}',
                'bypass_trafficLimitBytes': int(left_gb * GB)},
    })


async def bought(journal, user_id: int, price: int, gb: int,
                 days_ago: int = 1) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -price, 'kind': 'bypass',
        'at': now() - timedelta(days=days_ago), 'meta': {'gb': gb},
        'description': f'ByPass: {gb} Гб',
    })


async def collect(repos, panel):
    users, journal = repos
    return await bypass_usage.collect(users, journal, panel, SQUAD, START, END)


# ── расход ──────────────────────────────────────────────────────────────────
async def test_the_average_counts_only_those_who_actually_used_it(repos, db):
    """Подключили трое, качал один: средний расход — 6 Гб, а не 2."""
    users, journal = repos
    for user_id in (10, 11, 12):
        await subscriber(users, user_id)

    data = await collect(repos, Panel({'10': 6 * GB}))

    assert data['subscribers'] == 3 and data['spenders'] == 1
    assert bypass_usage.gb(data['used_bytes'] / data['spenders']) == 6.0


async def test_a_person_counted_twice_by_the_panel_is_counted_once(repos, db):
    """Панель отдаёт расход и по своему id, и по username — а username у нас
    telegram id. Без отбора средний расход удваивается."""
    users, journal = repos
    await subscriber(users, 10)

    data = await collect(repos, Panel({4271: 5 * GB, '10': 5 * GB}))

    assert data['spenders'] == 1 and bypass_usage.gb(data['used_bytes']) == 5.0


async def test_traffic_of_strangers_is_not_ours(repos, db):
    """В скваде могут оказаться и чужие подписки — считаем только своих."""
    users, journal = repos
    await subscriber(users, 10)

    data = await collect(repos, Panel({'10': 2 * GB, '999': 100 * GB}))

    assert bypass_usage.gb(data['used_bytes']) == 2.0


async def test_the_median_is_shown_next_to_the_average(repos, db):
    """Один человек на сотне гигабайт задирает среднее — медиана честнее."""
    users, journal = repos
    for user_id in (10, 11, 12):
        await subscriber(users, user_id)

    data = await collect(repos, Panel({'10': 1 * GB, '11': 2 * GB,
                                       '12': 100 * GB}))

    assert bypass_usage.gb(data['median_bytes']) == 2.0


async def test_a_silent_panel_does_not_break_the_report(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5)

    data = await collect(repos, Panel(broken=True))

    assert data['spenders'] == 0 and data['paid'] == 50


async def test_without_a_squad_the_panel_is_not_asked(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    panel = Panel({'10': GB})

    await bypass_usage.collect(users, journal, panel, '', START, END)

    assert panel.asked == []


# ── деньги ──────────────────────────────────────────────────────────────────
async def test_purchases_are_summed_per_person(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5)
    await bought(journal, 10, 90, 15)

    data = await collect(repos, Panel())

    assert data['paid'] == 140 and data['gb_bought'] == 20
    assert data['payers'] == 1 and data['purchases'] == 2


async def test_a_charge_is_income_even_though_it_is_written_with_a_minus(repos, db):
    """В журнале списание лежит со знаком минус — для нас это приход."""
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 170, 30)

    assert (await collect(repos, Panel()))['paid'] == 170


async def test_purchases_outside_the_period_are_not_counted(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5, days_ago=90)

    assert (await collect(repos, Panel()))['paid'] == 0


async def test_unspent_gigabytes_are_visible(repos, db):
    """Оплаченный, но не прокачанный трафик — обязательство, а не выручка."""
    users, journal = repos
    await subscriber(users, 10, left_gb=12)

    assert bypass_usage.gb((await collect(repos, Panel()))['left_bytes']) == 12.0


# ── приведение к месяцу ─────────────────────────────────────────────────────
def test_a_weekly_report_is_scaled_to_a_month():
    assert bypass_usage.per_month(7.0, days=7) == 30.0


def test_a_monthly_report_is_left_as_is():
    assert bypass_usage.per_month(12.0, days=30) == 12.0


# ── экран ───────────────────────────────────────────────────────────────────
async def test_the_report_says_the_squad_is_missing(repos, db):
    users, journal = repos
    await subscriber(users, 10)

    text = admin.render(await bypass_usage.collect(
        users, journal, Panel(), '', START, END))

    assert 'Сквад ByPass не задан' in text


async def test_the_report_warns_when_traffic_outruns_payment(repos, db):
    """Ровно то, что делает коэффициент 0.1: качают вдесятеро больше, чем
    списывается с лимита."""
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 50, 5)

    text = admin.render(await collect(repos, Panel({'10': 50 * GB})))

    assert 'Прокачано 50.0 Гб, оплачено 5 Гб' in text
    assert 'коэффициенте 0.1' in text


async def test_the_report_shows_the_monthly_average_per_person(repos, db):
    users, journal = repos
    await subscriber(users, 10)
    await bought(journal, 10, 300, 30)

    text = admin.render(await collect(repos, Panel({'10': 30 * GB})))

    assert '30.0 Гб</b> в месяц' in text
    assert '300₽</b> в месяц' in text
