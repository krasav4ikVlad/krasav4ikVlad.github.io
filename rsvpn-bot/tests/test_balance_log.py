"""Журнал движения денег: он должен видеть каждый рубль.

Панель оператора отвечает на один вопрос — «за что списали». Ответ есть
только если записана каждая операция, со знаком и с причиной. Пропущенная
строка выглядит как «деньги пропали»: в старой панели продление на 100₽
показывалось как «+100», то есть как пополнение.
"""

from datetime import timedelta

import pytest

from app.repositories.balance_log import BalanceLogRepository, guess_kind
from app.repositories.users import UsersRepository
from app.settings.service import SettingsService


@pytest.fixture
def users(db):
    repo = UsersRepository(db['users'])
    repo.journal = BalanceLogRepository(db['balance_log'])
    return repo


async def rows(db, user_id: int = 1) -> list[dict]:
    return await BalanceLogRepository(db['balance_log']).for_user(user_id)


# ── знак и содержание строки ────────────────────────────────────────────────
async def test_a_topup_is_written_with_a_plus(db, users, user_factory):
    await user_factory(**{'info.balance': 0})

    await users.credit(1, 100, 'Пополнение (cardlink), бонус 20₽', kind='topup')

    row = (await rows(db))[0]
    assert row['amount'] == 100 and row['direction'] == 'in'
    assert row['balance_after'] == 100
    assert row['kind'] == 'topup' and row['title'] == 'Пополнение'
    assert row['source'] == 'user' and row['account'] == 'balance'


async def test_a_charge_is_written_with_a_minus(db, users, user_factory):
    """Главное отличие от старой панели: списание — это минус, а не плюс."""
    await user_factory(**{'info.balance': 200})

    await users.charge(1, 75, 'Доп. устройства: 1 шт.', kind='devices')

    row = (await rows(db))[0]
    assert row['amount'] == -75 and row['direction'] == 'out'
    assert row['balance_after'] == 125
    assert row['kind'] == 'devices'


async def test_the_balance_after_is_taken_from_the_same_query(db, users, user_factory):
    """Читать баланс вторым запросом нельзя: между ними проходит чужое
    списание, и в истории окажется чужая цифра."""
    await user_factory(**{'info.balance': 500})

    await users.charge(1, 150, 'Покупка подписки «Месяц»', kind='plan')
    await users.charge(1, 6, 'Продление подписки «Ежедневная»', kind='renewal',
                       auto=True)

    assert [row['balance_after'] for row in await rows(db)] == [344, 350]


async def test_an_automatic_charge_is_marked_as_such(db, users, user_factory):
    """«Списал сам» и «списалось само» — разные вопросы в поддержку."""
    await user_factory(**{'info.balance': 100})

    await users.charge(1, 6, 'Продление подписки «Ежедневная»', auto=True,
                       kind='renewal')

    row = (await rows(db))[0]
    assert row['auto'] is True and row['source'] == 'auto'


async def test_a_failed_charge_writes_nothing(db, users, user_factory):
    """Денег не хватило — операции не было, и в журнале её быть не должно."""
    await user_factory(**{'info.balance': 10})

    assert await users.charge(1, 100, 'Покупка подписки', kind='plan') is False
    assert not await rows(db)


async def test_meta_keeps_the_details(db, users, user_factory):
    await user_factory(**{'info.balance': 0})

    await users.credit(1, 30, 'Промокод SORRY30', kind='promo',
                       meta={'code': 'SORRY30'})

    assert (await rows(db))[0]['meta'] == {'code': 'SORRY30'}


# ── категории ───────────────────────────────────────────────────────────────
async def test_a_missing_kind_is_guessed_from_the_description(db, users, user_factory):
    """Отчёт, построенный на подстроках описания, однажды тихо разъезжается.
    Поэтому категория есть у каждой строки, даже если её забыли передать."""
    await user_factory(**{'info.balance': 1000})

    await users.charge(1, 10, 'Продление подписки «Ежедневная»')
    await users.credit(1, 10, 'Возврат: панель не продлила подписку')

    assert [row['kind'] for row in await rows(db)] == ['refund', 'renewal']


def test_guessing_covers_every_kind_of_money():
    assert guess_kind('Пополнение (tribute), бонус 30₽') == 'topup'
    assert guess_kind('Активация промокода WELCOME') == 'promo'
    assert guess_kind('Реферальное начисление от друга 12') == 'referral'
    assert guess_kind('Бонус за возвращение') == 'campaign'
    assert guess_kind('Доп. устройства: 2 шт.') == 'devices'
    assert guess_kind('ByPass: 5 Гб') == 'bypass'
    assert guess_kind('Личный сервер «Мини»') == 'private_server'
    assert guess_kind('Возврат за доп. устройства') == 'refund', 'возврат важнее темы'
    assert guess_kind('Что-то новое') == 'other'


# ── места, которые двигают деньги мимо credit/charge ─────────────────────────
#
# Их три, и каждое делает это одним атомарным запросом ради защиты от гонок.
# Значит, журнал в них пишется отдельно — и легко забыть.

async def test_the_monthly_device_charge_is_journalled(db, users, user_factory):
    from app.core.time import now
    from app.services.devices import DeviceBillingService

    class Vpn:
        async def update_subscription(self, uuid, **kw):
            return {}

    settings = SettingsService(db['bot_settings'])
    service = DeviceBillingService(users, settings, Vpn())
    await user_factory(**{
        'info.balance': 500, 'vpn.uuid': 'u-1', 'vpn.hwidDeviceLimit': 3,
        'vpn.extraDevices': [{'id': 'p1', 'amount': 1, 'pricePerDevice': 75,
                              'active': True, 'nextChargeAt': now() - timedelta(days=1)}]})

    await service.run()

    row = (await rows(db))[0]
    assert row['amount'] == -75 and row['kind'] == 'devices' and row['auto'] is True


async def test_the_referral_reward_is_journalled(db, users, user_factory):
    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    settings = SettingsService(db['bot_settings'])
    service = TopupService(users, PaymentsRepository(db['payments']), settings)
    await user_factory(**{'info.balance': 0})                       # пригласивший
    await user_factory(**{'info.balance': 0, 'user_data.referrer': 1})  # друг

    await service.process(provider='cardlink', txid='t-1', amount=100, user_id=2)

    referral = [row for row in await rows(db, 1) if row['kind'] == 'referral']
    assert referral and referral[0]['amount'] > 0
    assert referral[0]['meta']['friend_id'] == 2
    # деньги упали на реферальный счёт, а не на баланс
    assert referral[0]['account'] == 'referral'


async def test_the_topup_itself_is_journalled_with_the_provider(db, users, user_factory):
    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    settings = SettingsService(db['bot_settings'])
    service = TopupService(users, PaymentsRepository(db['payments']), settings)
    await user_factory(**{'info.balance': 0})

    await service.process(provider='cardlink', txid='t-2', amount=100, user_id=1)

    row = (await rows(db))[0]
    assert row['kind'] == 'topup'
    assert row['meta']['paid'] == 100 and row['meta']['provider'] == 'cardlink'
    assert row['amount'] >= 100, 'бонус тоже часть начисления'
