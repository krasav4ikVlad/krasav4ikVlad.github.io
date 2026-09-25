"""Кто живёт на подарках — и предел на запасном сервере.

Главная проверка здесь одна: запасной сервер после истечения перестал
продлеваться бесконечно. Всё остальное — отчёты, по которым решение
принимает человек, поэтому проверяется, что они не врут в обе стороны:
не прячут сидящих годами и не записывают в накрутчики того, кто платит
за жену.
"""

from datetime import timedelta

import pytest

from app.admin import freebies as admin
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services import freebies
from app.services.lifeline import LifelineService
from app.settings.service import SettingsService


@pytest.fixture
def repos(db):
    return (UsersRepository(db['users']), PaymentsRepository(db['payments']),
            BalanceLogRepository(db['balance_log']))


class Panel:
    def __init__(self):
        self.updated: list[tuple] = []

    async def update_subscription(self, uuid, **kw):
        self.updated.append((uuid, kw))
        return {}


async def rider(users, user_id: int, days_ago: int, *, paid: bool = True,
                squads=('own',)) -> dict:
    """Человек, уже переведённый на запасной сервер столько-то дней назад."""
    return await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}'},
        'growth': {'has_topup': paid},
        'vpn': {'uuid': f'p-{user_id}', 'in_lifeline': True,
                'lifeline_at': now() - timedelta(days=days_ago),
                'orig_squads': list(squads),
                'activeInternalSquads': ['life']},
    })


# ── предел на запасном сервере ──────────────────────────────────────────────
@pytest.fixture
async def lifeline(db):
    users = UsersRepository(db['users'])
    settings = SettingsService(db['bot_settings'])
    await settings.set('lifeline.squad_uuid', 'life')
    panel = Panel()
    return LifelineService(users, settings, panel), users, settings, panel


async def test_a_fresh_expiry_moves_the_person_to_the_spare_server(lifeline):
    service, users, settings, panel = lifeline
    await users.create({
        'user_data': {'user_id': 1},
        'vpn': {'uuid': 'p-1', 'activeInternalSquads': ['own']},
    })

    result = await service.on_expired(await users.get(1))

    assert result['note'] == 'moved' and panel.updated


async def test_the_window_is_extended_while_within_the_limit(lifeline):
    service, users, settings, panel = lifeline
    await settings.set('lifeline.max_days', 14)
    await rider(users, 1, days_ago=3)

    result = await service.on_expired(await users.get(1))

    assert result['note'] == 'grace_extended' and panel.updated


async def test_after_the_limit_the_spare_server_is_not_extended(lifeline):
    """Раньше окно продлевалось на каждом истечении — то есть вечно."""
    service, users, settings, panel = lifeline
    await settings.set('lifeline.max_days', 14)
    await rider(users, 1, days_ago=40)

    result = await service.on_expired(await users.get(1))

    assert result['note'] == 'grace_over'
    assert panel.updated == [], 'панель трогать незачем — пусть подписка гаснет'


async def test_the_limit_counts_from_the_first_move_not_the_last(lifeline):
    """Иначе предел не наступит никогда: каждое продление обнуляло бы счёт."""
    service, users, settings, panel = lifeline
    await settings.set('lifeline.max_days', 7)
    await rider(users, 1, days_ago=30)
    await service.on_expired(await users.get(1))

    assert panel.updated == []


async def test_zero_means_no_limit_as_before(lifeline):
    service, users, settings, panel = lifeline
    await settings.set('lifeline.max_days', 0)
    await rider(users, 1, days_ago=400)

    assert (await service.on_expired(await users.get(1)))['note'] == 'grace_extended'


async def test_someone_moved_before_we_started_counting_is_not_kept_forever(lifeline):
    service, users, settings, panel = lifeline
    await settings.set('lifeline.max_days', 14)
    await users.create({
        'user_data': {'user_id': 1},
        'vpn': {'uuid': 'p-1', 'in_lifeline': True, 'orig_squads': ['own'],
                'activeInternalSquads': ['life']},
    })

    assert (await service.on_expired(await users.get(1)))['note'] == 'grace_over'


async def test_renewal_still_brings_the_person_back(lifeline):
    """Предел не должен мешать главному: человек продлился — сервера его."""
    service, users, settings, panel = lifeline
    await rider(users, 1, days_ago=40)
    await users.set_vpn(1, {'expireAt': now() + timedelta(days=30)})

    assert await service.restore(1) is True
    assert panel.updated[-1][1]['squads'] == ['own']


# ── отчёт по сидящим ────────────────────────────────────────────────────────
async def test_riders_are_counted_with_their_days(repos, db):
    users, payments, journal = repos
    await rider(users, 10, days_ago=2)
    await rider(users, 11, days_ago=120)

    data = await freebies.lifeline_riders(users, over_days=14)

    assert data['total'] == 2 and data['over'] == 1
    assert [row['user_id'] for row in data['rows']] == [11, 10]


async def test_those_who_never_paid_are_counted_apart(repos, db):
    """Никогда не плативший на запасном сервере — это не «клиент в паузе»."""
    users, payments, journal = repos
    await rider(users, 10, days_ago=30, paid=False)
    await rider(users, 11, days_ago=30, paid=True)

    assert (await freebies.lifeline_riders(users))['never_paid'] == 1


async def test_people_with_a_working_subscription_are_not_riders(repos, db):
    users, payments, journal = repos
    await users.create({'user_data': {'user_id': 10},
                        'vpn': {'uuid': 'p-10', 'in_lifeline': False}})

    assert (await freebies.lifeline_riders(users))['total'] == 0


# ── один кошелёк на много аккаунтов ─────────────────────────────────────────
async def payment(payments, user_id: int, amount: int, email: str = '',
                  status: str = 'done') -> None:
    await payments.col.insert_one({
        'txid': f'tx-{user_id}-{amount}-{email}', 'user_id': user_id,
        'amount': amount, 'status': status, 'created_at': now(),
        'payload': {'email': email} if email else {},
    })


async def test_one_wallet_on_two_accounts_is_a_group(repos, db):
    users, payments, journal = repos
    await payment(payments, 10, 500, 'One@mail.ru')
    await payment(payments, 11, 300, 'one@mail.ru')

    groups = await freebies.shared_payers(payments)

    assert len(groups) == 1
    assert groups[0]['user_ids'] == [10, 11] and groups[0]['amount'] == 800


async def test_one_person_paying_many_times_is_not_a_group(repos, db):
    users, payments, journal = repos
    await payment(payments, 10, 500, 'one@mail.ru')
    await payment(payments, 10, 500, 'one@mail.ru')

    assert await freebies.shared_payers(payments) == []


async def test_payments_without_any_identity_are_not_matched(repos, db):
    """Пустой отпечаток есть у всех — иначе «совпало» было бы у каждого."""
    users, payments, journal = repos
    await payment(payments, 10, 500)
    await payment(payments, 11, 500)

    assert await freebies.shared_payers(payments) == []


async def test_unfinished_payments_do_not_make_groups(repos, db):
    users, payments, journal = repos
    await payment(payments, 10, 500, 'one@mail.ru', status='new')
    await payment(payments, 11, 500, 'one@mail.ru')

    assert await freebies.shared_payers(payments) == []


async def test_the_widest_groups_come_first(repos, db):
    users, payments, journal = repos
    await payment(payments, 10, 100, 'a@mail.ru')
    await payment(payments, 11, 100, 'a@mail.ru')
    for user_id in (20, 21, 22):
        await payment(payments, user_id, 100, 'b@mail.ru')

    groups = await freebies.shared_payers(payments)

    assert groups[0]['accounts'] == 3


# ── окупаемость бонуса за возвращение ───────────────────────────────────────
async def credited(journal, user_id: int, amount: int, days_ago: int) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': amount, 'kind': 'campaign',
        'at': now() - timedelta(days=days_ago), 'description': 'Бонус за возвращение',
    })


async def test_a_bonus_that_brought_a_payment_is_counted_as_returned(repos, db):
    users, payments, journal = repos
    await credited(journal, 10, 40, days_ago=5)
    await payment(payments, 10, 300)

    data = await freebies.return_bonus(journal, payments,
                                       now() - timedelta(days=30), now())

    assert data['given'] == 40 and data['returned'] == 1 and data['revenue'] == 300


async def test_a_payment_made_before_the_bonus_does_not_count(repos, db):
    """Бонус, выданный тому, кто заплатил бы и так, ничего не купил."""
    users, payments, journal = repos
    await payments.col.insert_one({
        'txid': 'old', 'user_id': 10, 'amount': 300, 'status': 'done',
        'created_at': now() - timedelta(days=20), 'payload': {},
    })
    await credited(journal, 10, 40, days_ago=5)

    data = await freebies.return_bonus(journal, payments,
                                       now() - timedelta(days=30), now())

    assert data['returned'] == 0 and data['revenue'] == 0


async def test_bonuses_outside_the_period_are_not_counted(repos, db):
    users, payments, journal = repos
    await credited(journal, 10, 40, days_ago=90)

    data = await freebies.return_bonus(journal, payments,
                                       now() - timedelta(days=30), now())

    assert data['people'] == 0 and data['given'] == 0


# ── экран ───────────────────────────────────────────────────────────────────
async def test_the_report_says_when_the_spare_server_has_no_limit(repos, db):
    users, payments, journal = repos
    await rider(users, 10, days_ago=100)

    text = admin.render(await freebies.lifeline_riders(users), [],
                        {'people': 0, 'given': 0, 'returned': 0, 'revenue': 0},
                        30, max_days=0)

    assert 'Предел не задан' in text


async def test_the_report_counts_what_came_back_per_ruble(repos, db):
    text = admin.render({'total': 0, 'rows': [], 'over': 0, 'never_paid': 0},
                        [], {'people': 2, 'given': 100, 'returned': 1,
                             'revenue': 300}, 30, max_days=14)

    assert 'вернулось: <b>3.0₽</b>' in text


async def test_an_empty_report_does_not_divide_by_zero(repos, db):
    text = admin.render({'total': 0, 'rows': [], 'over': 0, 'never_paid': 0},
                        [], {'people': 0, 'given': 0, 'returned': 0,
                             'revenue': 0}, 30, max_days=14)

    assert 'Совпадений нет' in text
