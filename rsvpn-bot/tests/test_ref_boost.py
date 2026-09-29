"""Повышенный процент принадлежит человеку, а не ссылке.

«Эту неделю у тебя 50 вместо 30» рефовод понимает единственным способом:
неделю все его люди приносят по 50%. Все — и пришедшие по именной метке,
и по обычной числовой ссылке, и приглашённые год назад. Иначе выходит
несуразица: процент подняли, а половина его базы по-прежнему приносит по
тридцать, потому что пришла не по той ссылке.

Второе правило ровно такое же по важности: кончился срок — процент сам
вернулся к обычному, без единого действия руками.
"""

from datetime import timedelta

import pytest

from app.bot.callbacks import Menu
from app.core.time import now
from app.domain.ref_tags import best_boost
from app.repositories.payments import PaymentsRepository
from app.repositories.ref_tags import RefTagsRepository
from app.repositories.users import UsersRepository
from app.services import ref_boost
from app.services.topup import TopupService
from app.settings.service import SettingsService

from tests.test_bot_flow import TG_USER, callback, env  # noqa: F401

OWNER = 500


@pytest.fixture
def tags(db):
    return RefTagsRepository(db['ref_tags'])


@pytest.fixture
def users(db):
    return UsersRepository(db['users'])


@pytest.fixture
def settings(db):
    return SettingsService(db['bot_settings'])


@pytest.fixture
def topup(db, users, settings, tags):
    return TopupService(users, PaymentsRepository(db['payments']), settings,
                        ref_tags=tags)


async def boss(users) -> None:
    await users.create({'user_data': {'user_id': OWNER},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})


async def friend(users, user_id: int, *, tag: str = '',
                 referrer: int = OWNER) -> None:
    card = {'user_data': {'user_id': user_id, 'referrer': referrer},
            'info': {'balance': 0}, 'growth': {}}
    if tag:
        card['user_data']['ref_tag'] = tag
    await users.create(card)


async def boosted(tags, tag: str = 'vlad', *, percent: int = 50,
                  days: int = 7, owner: int = OWNER) -> None:
    await tags.create(tag, owner)
    await tags.update(tag, boost_rate=percent / 100,
                      boost_until=now() + timedelta(days=days))


async def reward(topup, user_id: int, amount: int = 1000,
                 txid: str = 't1') -> int:
    result = await topup.process(provider='wata', txid=txid, amount=amount,
                                 user_id=user_id)
    return result['referral_reward']


# ── акция достаётся всем его людям ──────────────────────────────────────────
async def test_an_old_referral_brings_the_raised_percent_too(users, tags, topup, db):
    """Главное. Человек приведён до всякой акции, метки в карточке у него
    нет — и всё равно на этой неделе он приносит 50%."""
    await boss(users)
    await friend(users, 1)                 # пришёл по числовой ссылке
    await boosted(tags)

    assert await reward(topup, 1) == 500


async def test_a_referral_of_another_tag_is_raised_as_well(users, tags, topup, db):
    """Метки у одного человека бывают разные — акция одна, его."""
    await boss(users)
    await friend(users, 1, tag='othertag')
    await tags.create('othertag', OWNER)
    await boosted(tags, 'vlad')

    assert await reward(topup, 1) == 500


async def test_the_tag_referral_still_gets_it(users, tags, topup, db):
    """Прежнее поведение никуда не делось."""
    await boss(users)
    await boosted(tags)
    await friend(users, 1, tag='vlad')

    assert await reward(topup, 1) == 500


async def test_someone_elses_referral_is_untouched(users, tags, topup, db):
    """Акция одного рефовода не должна поднимать процент другому."""
    await boss(users)
    await users.create({'user_data': {'user_id': 600},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await friend(users, 1, referrer=600)
    await boosted(tags)                    # метка у OWNER, а не у 600

    assert await reward(topup, 1) == 300


async def test_a_referrerless_payment_changes_nothing(users, tags, topup, db):
    await boosted(tags)
    await users.create({'user_data': {'user_id': 1}, 'info': {'balance': 0},
                        'growth': {}})

    assert await reward(topup, 1) == 0


# ── и перестаёт доставаться, когда срок вышел ───────────────────────────────
async def test_after_the_deadline_the_percent_returns_by_itself(users, tags,
                                                                topup, db):
    """Без единого действия руками: ставка не записана ни в чью карточку,
    она считается в момент оплаты."""
    await boss(users)
    await friend(users, 1)
    await tags.create('vlad', OWNER)
    await tags.update('vlad', boost_rate=0.5,
                      boost_until=now() - timedelta(minutes=1))

    assert await reward(topup, 1) == 300


async def test_the_same_person_pays_differently_before_and_after(users, tags,
                                                                 topup, db):
    """Тот же друг, та же сумма — до конца срока и после."""
    await boss(users)
    await friend(users, 1)
    await boosted(tags, days=7)
    during = await reward(topup, 1, txid='t-during')

    await tags.update('vlad', boost_until=now() - timedelta(minutes=1))
    after = await reward(topup, 1, txid='t-after')

    assert during == 500 and after == 300


async def test_what_was_paid_out_is_not_recalculated(users, tags, topup, db):
    """Проценты считаются в момент оплаты: конец акции не отнимает уже
    начисленное."""
    await boss(users)
    await friend(users, 1)
    await boosted(tags)
    await reward(topup, 1, txid='t-during')

    await tags.update('vlad', boost_until=now() - timedelta(minutes=1))

    earned = (await users.get(OWNER))['info']['ref_stats']['earned_total']
    assert earned == 500


# ── какая именно акция действует ────────────────────────────────────────────
def test_the_best_of_several_tags_wins():
    """Меток у человека несколько, акции на них могут не совпадать. Спорить,
    какая главная, незачем: обещано было большее число."""
    live = now() + timedelta(days=1)
    rows = [{'boost_rate': 0.4, 'boost_until': live},
            {'boost_rate': 0.6, 'boost_until': live},
            {'boost_rate': 0.5, 'boost_until': live}]

    assert best_boost(rows, now())['boost_rate'] == 0.6


def test_an_expired_tag_does_not_win_over_a_live_one():
    rows = [{'boost_rate': 0.9, 'boost_until': now() - timedelta(days=1)},
            {'boost_rate': 0.4, 'boost_until': now() + timedelta(days=1)}]

    assert best_boost(rows, now())['boost_rate'] == 0.4


def test_without_any_live_boost_there_is_nothing():
    assert best_boost([], now()) is None
    assert best_boost([{'boost_rate': 0.5}], now()) is None


async def test_a_boost_below_the_usual_percent_does_not_lower_it(users, tags,
                                                                 topup, db):
    """Понижать процент через акцию нельзя — она не для этого."""
    await boss(users)
    await friend(users, 1)
    await boosted(tags, percent=10)

    assert await reward(topup, 1) == 300


async def test_a_broken_tag_collection_does_not_break_the_payment(users, settings, db):
    """Акция — дело десятое: её неудача не должна стоить человеку
    начисления."""
    class Broken:
        async def of_user(self, user_id, limit=20):
            raise RuntimeError('mongo down')

    await boss(users)
    await friend(users, 1)
    topup = TopupService(users, PaymentsRepository(db['payments']), settings,
                         ref_tags=Broken())

    assert await reward(topup, 1) == 300


async def test_the_rate_is_the_usual_one_without_any_tags(users, tags, db):
    assert await ref_boost.rate_for(tags, OWNER, base=0.3) == 0.3
    assert await ref_boost.rate_for(None, OWNER, base=0.3) == 0.3
    assert await ref_boost.rate_for(tags, None, base=0.3) == 0.3


# ── человек видит свой процент ──────────────────────────────────────────────
#
# «Обещали 50, начислили 30» — худшее, что может случиться с рефоводом.
# Поэтому на экране рефералки стоит та же ставка, по которой считаются
# деньги, а не та, что записана в настройках.

async def test_the_screen_shows_the_raised_percent(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': TG_USER.id},
                          'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await boosted(c.ref_tags, 'vlad', percent=50, owner=TG_USER.id)

    await dp.feed_update(bot, callback(Menu(screen='referrals').pack()))

    assert '50%' in session.last_text and '30%' in session.last_text
    assert 'вернётся к обычному' in session.last_text


async def test_the_screen_shows_the_usual_percent_without_a_boost(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': TG_USER.id},
                          'info': {'balance': 0, 'ref_stats': {'referrals': []}}})

    await dp.feed_update(bot, callback(Menu(screen='referrals').pack()))

    assert 'Вы получаете 30%' in session.last_text
    assert 'Повышенный процент' not in session.last_text


async def test_an_expired_boost_is_not_promised_on_the_screen(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': TG_USER.id},
                          'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await c.ref_tags.create('vlad', TG_USER.id)
    await c.ref_tags.update('vlad', boost_rate=0.5,
                            boost_until=now() - timedelta(minutes=1))

    await dp.feed_update(bot, callback(Menu(screen='referrals').pack()))

    assert 'Вы получаете 30%' in session.last_text
