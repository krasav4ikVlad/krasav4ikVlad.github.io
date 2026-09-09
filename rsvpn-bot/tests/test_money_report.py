"""Отчёт по экономике: во сколько обходятся бонусы и рефералка.

Считать деньги по описаниям операций нельзя, поэтому проверяем именно
арифметику: приход берётся из платежей провайдеров, роздано — из разницы
между зачисленным и оплаченным плюс реферальные начисления, а долги — из
остатков на счетах.
"""

from datetime import timedelta

from app.admin.money import advice, collect, render, rub
from app.core.time import now


async def payment(db, amount, credited=None, provider='cardlink', user_id=1,
                  ago_days=1, status='done'):
    await db['payments'].insert_one({
        'txid': f'{user_id}-{amount}-{ago_days}-{provider}',
        'user_id': user_id, 'amount': amount,
        'credited': amount if credited is None else credited,
        'provider': provider, 'status': status,
        'created_at': now() - timedelta(days=ago_days),
    })


async def entry(db, amount, kind, ago_days=1, user_id=1):
    await db['balance_log'].insert_one({
        'user_id': user_id, 'at': now() - timedelta(days=ago_days),
        'amount': amount, 'direction': 'in' if amount >= 0 else 'out',
        'kind': kind, 'account': 'balance',
    })


def test_rubles_are_readable():
    assert rub(45300) == '45 300₽' and rub(0) == '0₽'


async def test_income_counts_only_real_payments(container, db):
    """Приход — это то, что человек заплатил, а не то, что ему зачислили."""
    await payment(db, 100, credited=125)
    await payment(db, 200, credited=250, user_id=2)
    await payment(db, 999, credited=999, user_id=3, status='new')   # не оплачен

    data = await collect(container, days=30)

    assert data['gross'] == 300, 'зачисленные бонусы — не выручка'
    assert data['credited'] == 375 and data['bonus'] == 75
    assert data['payments'] == 2 and len(data['payers']) == 2


async def test_payments_outside_the_period_are_left_out(container, db):
    await payment(db, 100, ago_days=2)
    await payment(db, 500, ago_days=40)

    data = await collect(container, days=30)

    assert data['gross'] == 100


async def test_an_earlier_payment_makes_the_payer_returning(container, db):
    """Новый плательщик и вернувшийся — разные вещи для решения, куда вкладывать."""
    await payment(db, 300, user_id=7, ago_days=90)
    await payment(db, 100, user_id=7, ago_days=1)
    await payment(db, 100, user_id=8, ago_days=1)

    data = await collect(container, days=30)

    assert len(data['payers']) == 2 and data['new_payers'] == 1


async def test_the_price_of_bonuses_and_referrals_is_summed(container, db):
    """Главная цифра экрана: сколько обязательств на рубль прихода."""
    await payment(db, 1000, credited=1200)
    await entry(db, 300, 'referral')

    data = await collect(container, days=30)

    assert data['given'] == 500, 'бонус 200 плюс рефералка 300'
    assert data['obligations'] == 1500
    assert '1.50₽ обязательств' in render(data)


async def test_spending_is_grouped_by_kind(container, db):
    await entry(db, -150, 'plan')
    await entry(db, -75, 'devices')
    await entry(db, -75, 'devices')
    await entry(db, -500, 'payout')

    data = await collect(container, days=30)

    assert data['spend'] == {'plan': 150, 'devices': 150, 'payout': 500}
    assert data['payout'] == 500


async def test_debts_come_from_the_referral_field_that_is_actually_used(container, db,
                                                                       user_factory):
    """Начисление кладёт деньги в withdrawable — по нему и считаем."""
    await user_factory(**{'user_data.user_id': 50, 'info.balance': 400,
                          'info.ref_stats.withdrawable': 900})

    data = await collect(container, days=30)

    assert data['balances'] == 400 and data['ref_balance'] == 900


async def test_a_young_journal_is_admitted_not_reported_as_zero(container, db):
    """Ноль по журналу за прошлый год — это не «рефералки не было»."""
    await payment(db, 1000, ago_days=200)
    await entry(db, 300, 'referral', ago_days=1)

    fresh = await collect(container, days=90)
    assert fresh['journal_covers'] is False
    assert 'Журнал баланса ведётся с' in render(fresh)

    short = await collect(container, days=1)
    assert short['journal_covers'] is True


async def test_the_report_survives_an_empty_bot(container, db):
    """Свежая база не должна ронять экран делением на ноль."""
    data = await collect(container, days=30)

    assert data['gross'] == 0
    assert 'Экономика за 30 дн.' in render(data)


# ── подсказки ───────────────────────────────────────────────────────────────
def base(**over) -> dict:
    data = {'gross': 1000, 'bonus': 0, 'referral': 0, 'obligations': 1000,
            'active_subs': 0, 'payers': set(), 'new_payers': 0}
    data.update(over)
    return data


def test_an_expensive_bonus_is_named_with_its_price():
    tips = ' '.join(advice(base(bonus=300, obligations=1300)))

    assert '300₽' in tips and '30%' in tips
    assert '100₽' in tips, 'что даст снижение — тоже цифрой'


def test_a_healthy_economy_gets_no_lecture():
    assert advice(base(bonus=50, obligations=1050)) == []


def test_growth_only_on_new_payers_is_called_out():
    tips = ' '.join(advice(base(payers={1, 2, 3}, new_payers=3, active_subs=3)))

    assert 'Повторных плательщиков' in tips
