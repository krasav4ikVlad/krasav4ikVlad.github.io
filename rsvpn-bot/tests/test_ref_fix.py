"""Починка рефералов, потерянных из-за отсутствующей метки.

Случай настоящий: у блогера ссылка `?start=ref_RepublickCheck` была зашита
в старом боте, в новый метка не переехала — и полтора месяца люди
приходили, а пригласившего бот не знал. Ссылка при этом работала: Telegram
передавал её в /start, и бот сохранял целиком в `user_data.utm`. По нему
потерянные и находятся.

Главные правила здесь два: не начислить дважды и не трогать тех, у кого
всё и так посчитано.
"""

from datetime import timedelta

import pytest

from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.repositories.users import UsersRepository
from app.services import ref_fix

SINCE = now() - timedelta(days=50)
OWNER = 7996131040
TAG = 'republickcheck'


@pytest.fixture
def repos(db):
    users = UsersRepository(db['users'])
    journal = BalanceLogRepository(db['balance_log'])
    # как в контейнере: без журнала начисления не попадут в историю
    users.journal = journal
    return users, journal


async def came(users, user_id: int, *, utm: str = 'ref_RepublickCheck',
               days_ago: int = 30, referrer=None) -> None:
    await users.create({
        'user_data': {'user_id': user_id, 'username': f'u{user_id}',
                      'utm': utm, 'referrer': referrer or '',
                      'date_joined': now() - timedelta(days=days_ago)},
        'info': {'balance': 0, 'ref_stats': {'referrals': []}},
    })


async def topped_up(journal, user_id: int, paid: int, bonus: int = 0) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': paid + bonus, 'kind': 'topup',
        'at': now() - timedelta(days=5),
        'meta': {'paid': paid, 'bonus': bonus, 'provider': 'wata'}})


async def owner_card(users) -> dict:
    await users.create({'user_data': {'user_id': OWNER},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    return await users.get(OWNER)


async def look(repos) -> dict:
    users, journal = repos
    return await ref_fix.find(users, journal, tag=TAG, since=SINCE,
                              owner_id=OWNER)


# ── поиск ───────────────────────────────────────────────────────────────────
async def test_the_lost_are_found_by_their_start_link(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1)

    assert [row['user_id'] for row in (await look(repos))['fresh']] == [1]


async def test_the_link_is_matched_regardless_of_case(repos, db):
    """Люди копируют ссылку как придётся, Telegram отдаёт как есть."""
    users, journal = repos
    await owner_card(users)
    await came(users, 1, utm='ref_republickcheck')
    await came(users, 2, utm='ref_RepublickCheck')

    assert len((await look(repos))['fresh']) == 2


async def test_someone_elses_link_is_not_touched(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1, utm='ref_othertag')
    await came(users, 2, utm='post_2209_1430')

    assert (await look(repos))['fresh'] == []


async def test_those_who_came_before_the_date_are_skipped(repos, db):
    """До поломки всё считалось правильно, и трогать это нельзя."""
    users, journal = repos
    await owner_card(users)
    await came(users, 1, days_ago=100)

    assert (await look(repos))['fresh'] == []


async def test_those_with_a_referrer_are_counted_apart(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1, referrer=OWNER)

    data = await look(repos)
    assert data['fresh'] == [] and data['already'] == 1


async def test_the_percent_counts_from_what_was_paid_not_credited(repos, db):
    """Бонус за пополнение — наш подарок, платить с него процент не за что."""
    users, journal = repos
    await owner_card(users)
    await came(users, 1)
    await topped_up(journal, 1, paid=1000, bonus=200)

    assert (await look(repos))['paid'] == 1000


# ── починка ─────────────────────────────────────────────────────────────────
async def test_the_link_is_restored(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1)

    await ref_fix.repair(users, await look(repos), rate=0.3)

    card = await users.get(1)
    assert card['user_data']['referrer'] == OWNER
    assert card['user_data']['ref_tag'] == TAG


async def test_the_money_lands_on_the_referral_balance(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1)
    await topped_up(journal, 1, paid=1000)

    done = await ref_fix.repair(users, await look(repos), rate=0.3)

    stats = (await users.get(OWNER))['info']['ref_stats']
    assert done['reward'] == 300 and stats['withdrawable'] == 300
    assert stats['earned_total'] == 300 and 1 in stats['referrals']


async def test_a_second_run_does_not_pay_twice(repos, db):
    """Команду зовут с телефона, и второе «на всякий случай» не должно
    удвоить деньги."""
    users, journal = repos
    await owner_card(users)
    await came(users, 1)
    await topped_up(journal, 1, paid=1000)

    await ref_fix.repair(users, await look(repos), rate=0.3)
    again = await ref_fix.repair(users, await look(repos), rate=0.3)

    assert again['linked'] == 0 and again['reward'] == 0
    assert (await users.get(OWNER))['info']['ref_stats']['withdrawable'] == 300


async def test_someone_who_never_paid_is_still_linked(repos, db):
    """Связь нужна и без денег: его будущие пополнения пойдут в зачёт."""
    users, journal = repos
    await owner_card(users)
    await came(users, 1)

    done = await ref_fix.repair(users, await look(repos), rate=0.3)

    assert done['linked'] == 1 and done['reward'] == 0
    assert (await users.get(1))['user_data']['referrer'] == OWNER


async def test_the_journal_explains_where_the_money_came_from(repos, db):
    users, journal = repos
    await owner_card(users)
    await came(users, 1)
    await topped_up(journal, 1, paid=1000)

    await ref_fix.repair(users, await look(repos), rate=0.3)

    rows = [row for row in db['balance_log'].docs
            if row.get('user_id') == OWNER]
    assert rows and 'починка' in rows[0]['description']
    assert rows[0]['kind'] == 'referral'


async def test_the_mark_stops_a_repair_that_slipped_through(repos, db):
    """Вторая защита от двойного начисления, отдельно от первой.

    Первая — «у него уже есть пригласивший» — снимает большинство случаев,
    но её можно обойти, сняв связь руками. Поэтому починка ещё раз смотрит
    на отметку в момент записи. Здесь список подсунут вручную, потому что
    честный поиск такого человека и не вернёт.
    """
    users, journal = repos
    await owner_card(users)
    await came(users, 1)
    await topped_up(journal, 1, paid=1000)
    await ref_fix.repair(users, await look(repos), rate=0.3)
    await users.col.update_one({'user_data.user_id': 1},
                               {'$set': {'user_data.referrer': ''}})

    again = await ref_fix.repair(
        users,
        {'tag': TAG, 'since': SINCE, 'owner_id': OWNER,
         'fresh': [{'user_id': 1, 'username': 'u1', 'at': now(),
                    'paid': 1000, 'fixed_at': None}]},
        rate=0.3)

    assert again['reward'] == 0
    assert (await users.get(OWNER))['info']['ref_stats']['withdrawable'] == 300
