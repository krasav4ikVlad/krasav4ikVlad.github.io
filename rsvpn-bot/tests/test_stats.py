"""Статистика админки: быстрый путь, запасной и кэш.

Экран /admin открывался долго, потому что бот вычитывал каждый документ
пользователя вместе с `info.transactions`. Теперь считает Mongo, а перебор
остался запасным путём — и он обязан давать те же числа.
"""

from datetime import timedelta

import pytest

from app.admin.stats import StatsService, collect_stats
from app.core.time import now


class AggregateCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class ServerSide:
    """Коллекция, которая умеет aggregate — как настоящая Mongo."""

    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows
        self.error = error
        self.pipelines: list = []

    def aggregate(self, pipeline, **kwargs):
        self.pipelines.append(pipeline)
        if self.error:
            raise self.error
        return AggregateCursor(self.rows)


class Repo:
    def __init__(self, col):
        self.col = col


FACET = [{
    'totals': [{
        '_id': None, 'total_users': 213000, 'total_subs': 1200, 'active_subs': 900,
        'active_with_topup': 700, 'balance_active': 45000, 'balance_no_active': 1200,
        'ref_balance_sum': 3400,
    }],
    'segments': [
        {'_id': 'active_paid', 'n': 700},
        {'_id': 'new_trial_d0', 'n': 120},
        {'_id': 'странный сегмент', 'n': 5},
    ],
}]


async def test_server_side_numbers_are_read_as_is():
    data = await collect_stats(Repo(ServerSide(FACET)), (1,))

    assert data['total_users'] == 213000
    assert data['active_subs'] == 900
    assert data['balance_active'] == 45000
    assert data['segments']['active_paid'] == 700
    assert data['segments']['new_trial_d0'] == 120


async def test_unknown_segment_is_counted_separately():
    """Сегмент, которого нет в подписях, не должен молча пропадать."""
    data = await collect_stats(Repo(ServerSide(FACET)), (1,))
    assert data['unknown'] == 5


async def test_empty_database_gives_zeros():
    data = await collect_stats(Repo(ServerSide([{'totals': [], 'segments': []}])), (1,))

    assert data['total_users'] == 0
    assert data['unknown'] == 0
    assert set(data['segments'].values()) == {0}


async def test_transactions_are_not_requested_from_the_server():
    """Именно этот массив и делал экран медленным."""
    col = ServerSide(FACET)
    await collect_stats(Repo(col), (1,))

    assert 'transactions' not in str(col.pipelines[0])


# ── запасной путь ───────────────────────────────────────────────────────────
async def test_broken_aggregation_falls_back_instead_of_breaking_the_panel(db, user_factory):
    """Админка должна открыться медленно, но открыться."""
    col = ServerSide(error=RuntimeError('unsupported stage $facet'))
    col.find = db['users'].find                     # перебор пойдёт по заглушке
    await user_factory(**{'info.balance': 100})

    data = await collect_stats(Repo(col), ())

    assert data['total_users'] == 1
    assert data['balance_no_active'] == 100


async def test_fallback_matches_the_server_side_shape(db, user_factory):
    """У обоих путей одинаковый набор полей — иначе экран упадёт на KeyError."""
    await user_factory()
    slow = await collect_stats(Repo(db['users']), ())
    fast = await collect_stats(Repo(ServerSide(FACET)), ())

    assert set(slow) == set(fast)


async def test_admins_are_excluded_from_the_slices(db, user_factory):
    """Свой баланс в «балансах пользователей» видеть незачем."""
    await user_factory(**{'info.balance': 1000})     # user_id = 1, он же админ
    await user_factory(**{'info.balance': 50})

    data = await collect_stats(Repo(db['users']), (1,))

    assert data['total_users'] == 2                  # в общем счётчике админ есть
    assert data['balance_no_active'] == 50           # а в срезе баланса — нет


async def test_active_subscription_is_counted_by_the_expiry_date(db, user_factory):
    await user_factory(**{'vpn.shortUuid': 's-1', 'vpn.expireAt': now() + timedelta(days=5)})
    await user_factory(**{'vpn.shortUuid': 's-2', 'vpn.expireAt': now() - timedelta(days=5)})

    data = await collect_stats(Repo(db['users']), ())

    assert data['total_subs'] == 2
    assert data['active_subs'] == 1


# ── кэш ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def counting_repo(db):
    repo = Repo(db['users'])
    repo.col.calls = 0
    original = repo.col.find

    def find(*args, **kwargs):
        repo.col.calls += 1
        return original(*args, **kwargs)

    repo.col.find = find
    return repo


async def test_repeated_opens_do_not_recount(counting_repo):
    service = StatsService(counting_repo, ())

    await service.text()
    await service.text()
    await service.text()

    assert counting_repo.col.calls == 1


async def test_refresh_button_recounts(counting_repo):
    service = StatsService(counting_repo, ())

    await service.text()
    await service.text(force=True)

    assert counting_repo.col.calls == 2


async def test_cache_expires(counting_repo):
    service = StatsService(counting_repo, (), ttl=0)

    await service.text()
    await service.text()

    assert counting_repo.col.calls == 2
