"""Сколько приносит активный покупатель ByPass.

Два обстоятельства делают прямое деление неверным, и оба проверяются здесь:
покупают редко и помногу (значит, месяц — не та мерка), и половина базы
давно ушла (значит, её нельзя держать в среднем).
"""

from datetime import timedelta

import pytest

from app.admin import bypass_report as admin
from app.core.time import now
from app.repositories.balance_log import BalanceLogRepository
from app.services import bypass_arpu


@pytest.fixture
def journal(db):
    return BalanceLogRepository(db['balance_log'])


async def bought(journal, user_id: int, price: int, days_ago: int,
                 gb: int = 5) -> None:
    await journal.col.insert_one({
        'user_id': user_id, 'amount': -price, 'kind': 'bypass',
        'at': now() - timedelta(days=days_ago), 'meta': {'gb': gb},
        'description': f'ByPass: {gb} Гб'})


# ── ставка на человека ──────────────────────────────────────────────────────
async def test_a_big_package_is_spread_over_its_own_span(journal, db):
    """Взял 100 ГБ за 500₽ два месяца назад — это 250₽ в месяц, а не 500 в
    один месяц и ноль в другой."""
    await bought(journal, 10, 500, days_ago=60, gb=100)

    row = (await bypass_arpu.collect(journal))['rows'][0]

    assert row['rate'] == 250


async def test_small_frequent_purchases_give_a_comparable_number(journal, db):
    """По 50₽ каждую неделю — примерно те же две сотни в месяц, что и у
    того, кто берёт большой пакет раз в два месяца. Ради этого сравнения
    ставка и считается по сроку, а не по календарному месяцу."""
    for week in range(8):
        await bought(journal, 10, 50, days_ago=7 * week)

    row = (await bypass_arpu.collect(journal))['rows'][0]

    assert row['rate'] == 245     # 400₽ за 49 дней


async def test_yesterdays_purchase_is_not_a_fortune(journal, db):
    """Иначе вчерашние 500₽ превращались бы в 15 000₽ в месяц."""
    await bought(journal, 10, 500, days_ago=1)

    row = (await bypass_arpu.collect(journal))['rows'][0]

    assert row['rate'] == 500


async def test_the_span_counts_from_the_first_purchase(journal, db):
    await bought(journal, 10, 300, days_ago=90)
    await bought(journal, 10, 300, days_ago=10)

    row = (await bypass_arpu.collect(journal))['rows'][0]

    assert row['span_days'] == 90 and row['rate'] == 200


# ── кто активен ─────────────────────────────────────────────────────────────
async def test_someone_who_paid_half_a_year_ago_is_not_counted(journal, db):
    """Ровно то, из-за чего среднее и врёт."""
    await bought(journal, 10, 300, days_ago=10)
    await bought(journal, 11, 300, days_ago=180)

    data = await bypass_arpu.collect(journal)

    assert data['active'] == 1 and data['gone'] == 1


async def test_the_active_window_can_be_narrowed(journal, db):
    await bought(journal, 10, 300, days_ago=45)

    wide = await bypass_arpu.collect(journal, active_days=60)
    narrow = await bypass_arpu.collect(journal, active_days=30)

    assert wide['active'] == 1 and narrow['active'] == 0


async def test_a_big_package_two_months_ago_still_counts_as_active(journal, db):
    """Окно в два месяца выбрано ради него: он живой и платящий, просто
    ему хватило надолго."""
    await bought(journal, 10, 500, days_ago=55, gb=100)

    assert (await bypass_arpu.collect(journal))['active'] == 1


async def test_the_gone_are_shown_by_how_long_ago(journal, db):
    await bought(journal, 10, 300, days_ago=5)
    await bought(journal, 11, 300, days_ago=100)
    await bought(journal, 12, 300, days_ago=200)

    data = await bypass_arpu.collect(journal)

    assert data['gone'] == 2
    assert [(row['from'], row['people']) for row in data['gone_buckets']] == [
        (90, 1), (180, 1)]


# ── средние ─────────────────────────────────────────────────────────────────
async def test_the_average_and_the_median_are_both_shown(journal, db):
    """Один человек на большой ставке задирает среднее — медиана честнее."""
    await bought(journal, 10, 100, days_ago=5)
    await bought(journal, 11, 200, days_ago=5)
    await bought(journal, 12, 3000, days_ago=5)

    data = await bypass_arpu.collect(journal)

    assert data['average'] == 1100 and data['median'] == 200


async def test_the_simple_rate_matches_the_till(journal, db):
    """Вторая ставка — выручка за прошлый месяц на активного: она совпадает
    с кассой, в отличие от усреднённой по срокам."""
    await bought(journal, 10, 400, days_ago=10)
    await bought(journal, 11, 600, days_ago=10)

    data = await bypass_arpu.collect(journal)

    assert data['revenue_30'] == 1000 and data['simple'] == 500


async def test_old_purchases_do_not_leak_into_the_last_month(journal, db):
    await bought(journal, 10, 400, days_ago=5)
    await bought(journal, 10, 900, days_ago=50)

    data = await bypass_arpu.collect(journal)

    assert data['revenue_30'] == 400


async def test_the_interval_between_purchases_is_the_median(journal, db):
    await bought(journal, 10, 50, days_ago=30)
    await bought(journal, 10, 50, days_ago=20)
    await bought(journal, 10, 50, days_ago=10)

    assert (await bypass_arpu.collect(journal))['gap'] == 10.0


async def test_traffic_is_averaged_the_same_way(journal, db):
    await bought(journal, 10, 500, days_ago=60, gb=100)

    assert (await bypass_arpu.collect(journal))['gb_average'] == 50.0


# ── экран ───────────────────────────────────────────────────────────────────
async def test_an_empty_report_says_so(journal, db):
    text = admin.render_average(await bypass_arpu.collect(journal))

    assert 'Активных покупателей нет' in text


async def test_the_report_answers_the_question_in_one_line(journal, db):
    await bought(journal, 10, 300, days_ago=30)

    text = admin.render_average(await bypass_arpu.collect(journal))

    assert 'В среднем 300₽ в месяц' in text


async def test_the_report_shows_what_the_gone_would_have_done(journal, db):
    """Полезнее всего именно сравнение: видно, насколько ушедшие занижают."""
    await bought(journal, 10, 600, days_ago=5)
    await bought(journal, 11, 60, days_ago=200)

    text = admin.render_average(await bypass_arpu.collect(journal))

    assert 'Отсеяны как ушедшие' in text
    assert 'вместо 600₽' in text


# ── сколько купили за период ────────────────────────────────────────────────
async def test_purchases_are_totalled_by_window(journal, db):
    """Сутки, неделя, месяц, два месяца — касса за период, без приведения
    к месяцу: сравнивать их друг с другом — дело читателя."""
    await bought(journal, 10, 50, days_ago=0, gb=5)
    await bought(journal, 11, 90, days_ago=3, gb=15)
    await bought(journal, 12, 170, days_ago=20, gb=30)
    await bought(journal, 13, 500, days_ago=50, gb=100)

    windows = {row['days']: row for row in (await bypass_arpu.collect(journal))['sold']}

    assert (windows[1]['gb'], windows[1]['money']) == (5, 50)
    assert (windows[7]['gb'], windows[7]['money']) == (20, 140)
    assert (windows[30]['gb'], windows[30]['money']) == (50, 310)
    assert (windows[60]['gb'], windows[60]['money']) == (150, 810)


async def test_a_window_counts_people_not_purchases(journal, db):
    """Один человек с тремя покупками за сутки — это один человек."""
    for _ in range(3):
        await bought(journal, 10, 50, days_ago=0, gb=5)

    day = next(row for row in (await bypass_arpu.collect(journal))['sold']
               if row['days'] == 1)

    assert day['purchases'] == 3 and day['people'] == 1


async def test_older_purchases_stay_outside_every_window(journal, db):
    await bought(journal, 10, 500, days_ago=100, gb=100)

    assert all(row['gb'] == 0 for row in (await bypass_arpu.collect(journal))['sold'])


async def test_the_report_shows_the_windows(journal, db):
    await bought(journal, 10, 90, days_ago=2, gb=15)

    text = admin.render_average(await bypass_arpu.collect(journal))

    assert 'за сутки: <b>0 Гб</b>' in text
    assert 'за неделю: <b>15 Гб</b> на <b>90₽</b>' in text


# ── баланс против настоящих денег ───────────────────────────────────────────
class Payments:
    """Платежи провайдеру: сколько человек заплатил и сколько ему зачислили."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    async def iterate(self, query, projection=None):
        for row in self.rows:
            yield row


def payment(paid: int, credited: int) -> dict:
    return {'amount': paid, 'credited': credited, 'status': 'done',
            'created_at': now()}


async def test_balance_spent_is_not_money_earned(journal, db):
    """Пополнил 100₽ — получил 120₽ баланса. Значит «продали на 120» и
    «заработали 120» — разные суммы, и вторая меньше на бонус."""
    await bought(journal, 10, 120, days_ago=2, gb=15)
    payments = Payments([payment(100, 120)])

    data = await bypass_arpu.collect(journal, payments=payments)
    month = next(row for row in data['sold'] if row['days'] == 30)

    assert data['cash']['ratio'] == 0.833 and month['cash'] == 100


async def test_an_old_payment_without_the_field_is_not_invented(journal, db):
    """Нет записи о зачисленном — считаем, что зачислили оплаченное.
    Это завышает долю, но не выдумывает бонус, которого мы не знаем."""
    await bought(journal, 10, 100, days_ago=2, gb=15)
    payments = Payments([{'amount': 100, 'status': 'done', 'created_at': now()}])

    assert (await bypass_arpu.collect(journal, payments=payments))['cash']['ratio'] == 1.0


async def test_without_payments_the_share_is_unknown(journal, db):
    await bought(journal, 10, 100, days_ago=2, gb=15)

    assert (await bypass_arpu.collect(journal))['cash']['known'] is False


async def test_the_report_counts_what_is_left_after_the_servers(journal, db):
    await bought(journal, 10, 1200, days_ago=2, gb=200)
    payments = Payments([payment(1000, 1200)])

    text = admin.render_average(
        await bypass_arpu.collect(journal, payments=payments), cost_month=400)

    assert 'Из них настоящих денег: <b>1000₽</b>' in text
    assert 'Остаётся: +600₽ в месяц' in text


async def test_the_report_admits_it_does_not_know_the_server_price(journal, db):
    await bought(journal, 10, 100, days_ago=2, gb=15)

    text = admin.render_average(await bypass_arpu.collect(journal))

    assert 'Плата за серверы не задана' in text
