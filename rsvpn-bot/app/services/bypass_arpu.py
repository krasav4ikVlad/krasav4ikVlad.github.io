"""Сколько приносит активный покупатель ByPass. Только журнал, без панели.

Вопрос простой — «сколько человек платит за трафик в месяц», — но два
обстоятельства делают прямое деление неверным.

**Покупают редко и помногу.** Один берёт 100 ГБ и два месяца не заходит,
другой — по 5 ГБ каждую неделю. Если делить выручку за месяц на всех
покупавших, первый в один месяц выглядит богачом, а в следующий — нулём,
хотя платит ровно столько же. Поэтому ставка считается по каждому за его
собственный срок: сколько он заплатил от первой покупки до сегодня,
приведённое к месяцу.

**Половина базы давно ушла.** Тот, кто последний раз покупал полгода
назад, к нынешней выручке отношения не имеет, но тянет среднее вниз.
Поэтому в расчёт идут только активные — купившие хотя бы раз за последние
N дней; остальные считаются отдельно, как ушедшие.

Срок берётся не меньше месяца: человек, купивший вчера пакет на 500₽, не
приносит 15 000₽ в месяц — он просто только начал.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.core.time import now as time_now
from app.core.time import parse_dt

log = logging.getLogger(__name__)

# Активным считаем того, кто покупал за это время. Два месяца, а не месяц:
# самый обычный сценарий — «взял 100 ГБ и хватило надолго», и в окно в
# 30 дней такой человек не попадает, хотя он живой и платящий.
ACTIVE_DAYS = 60

# Минимальный срок для личной ставки — иначе вчерашняя покупка превращается
# в фантастические деньги в месяц.
MIN_SPAN_DAYS = 30

# Сколько истории читаем: нужна и для ушедших, и для первой покупки.
HISTORY_DAYS = 365

RATE_BUCKETS = ((0, 100), (100, 300), (300, 600), (600, 0))
GONE_BUCKETS = ((0, 30), (30, 60), (60, 90), (90, 180), (180, 0))

# Окна для строки «сколько купили»: сутки, неделя, месяц, два месяца.
# Считаются как есть, без приведения к месяцу: это касса за период, и
# сравнивать их друг с другом — работа читателя, а не отчёта.
WINDOWS = ((1, 'за сутки'), (7, 'за неделю'), (30, 'за месяц'),
           (60, 'за два месяца'))


async def collect(journal, active_days: int = ACTIVE_DAYS,
                  now: datetime | None = None, payments=None) -> dict:
    moment = now or time_now()
    people, sold = await _people(journal, moment - timedelta(days=HISTORY_DAYS),
                                 moment)
    cash = await _cash_ratio(payments, moment - timedelta(days=30), moment)
    for window in sold:
        window['cash'] = round(window['money'] * cash['ratio'])

    active, gone = [], []
    for row in people.values():
        row['days_since'] = (moment - row['last']).days
        row['span_days'] = max(MIN_SPAN_DAYS, (moment - row['first']).days)
        row['rate'] = round(row['spent'] * 30 / row['span_days'])
        row['gb_rate'] = round(row['gb'] * 30 / row['span_days'], 1)
        (active if row['days_since'] <= active_days else gone).append(row)

    rates = sorted(row['rate'] for row in active)
    recent = sum(row['spent_30'] for row in active)

    return {
        'active_days': active_days,
        'sold': sold,
        'cash': cash,
        'active': len(active),
        'gone': len(gone),
        'rows': sorted(active, key=lambda row: -row['rate']),
        # Две ставки на один вопрос, и обе честные.
        #   average — по личным ставкам: «сколько платит типичный активный»;
        #   simple  — выручка за 30 дней на активного: «сколько они принесли
        #             на самом деле за прошлый месяц».
        # Первая ровнее, вторая совпадает с кассой. Расходятся они, когда
        # месяц выдался особенным, — и это само по себе полезно видеть.
        'average': round(sum(rates) / len(rates)) if rates else 0,
        'median': _median(rates),
        'simple': round(recent / len(active)) if active else 0,
        'revenue_30': recent,
        'gb_average': round(sum(row['gb_rate'] for row in active) / len(active), 1)
                      if active else 0.0,
        'purchases': sum(row['count'] for row in active),
        'gap': _median([row['gap'] for row in active if row['gap']]),
        'buckets': _buckets(active),
        'gone_buckets': _gone_buckets(gone),
        'gone_rate': round(sum(row['rate'] for row in gone) / len(gone)) if gone else 0,
    }


async def _cash_ratio(payments, start: datetime, end: datetime) -> dict:
    """Какая доля баланса — настоящие деньги.

    Гигабайты покупают с баланса, а баланс приходит с бонусом: пополнил
    100₽ — получил 120₽. Значит «продали на 530 тысяч баланса» и «заработали
    530 тысяч» — разные вещи, и вторая меньше на бонус.

    Доля считается по платежам за тот же месяц: сколько человек заплатил
    против того, сколько ему зачислили. Реферальные начисления и бонусы
    кампаний сюда не входят вовсе — они зачисляются мимо платежей, поэтому
    настоящая доля чуть ниже посчитанной. Это честнее, чем гадать.
    """
    found = {'paid': 0, 'credited': 0, 'ratio': 1.0, 'known': False}
    if payments is None:
        return found

    async for row in payments.iterate(
            {'status': 'done', 'created_at': {'$gte': start, '$lte': end}},
            {'amount': 1, 'credited': 1}):
        amount = int(row.get('amount') or 0)
        found['paid'] += amount
        # Нет поля credited — платёж старый, до того как его стали писать:
        # считаем, что зачислили ровно оплаченное. Это завышает долю, но не
        # выдумывает бонус, которого мы не знаем.
        found['credited'] += int(row.get('credited') or amount)

    if found['credited']:
        found['ratio'] = round(found['paid'] / found['credited'], 3)
        found['known'] = True
    return found


async def _people(journal, start: datetime,
                  end: datetime) -> tuple[dict[int, dict], list[dict]]:
    """Покупки трафика по людям и итоги по окнам — одним проходом.

    Итоги считаются здесь же, а не вторым запросом: журнал и так читается
    целиком, а «сколько купили за сутки» — тот же самый список строк,
    отобранный по дате.
    """
    rows: dict[int, dict] = {}
    month_ago = end - timedelta(days=30)
    sold = [{'days': days, 'title': title, 'gb': 0, 'money': 0,
             'purchases': 0, 'people': set()} for days, title in WINDOWS]

    async for row in journal.iterate(
            {'kind': 'bypass', 'at': {'$gte': start, '$lte': end}},
            {'user_id': 1, 'amount': 1, 'meta': 1, 'at': 1}):
        user_id = row.get('user_id')
        at = parse_dt(row.get('at'))
        if user_id is None or at is None:
            continue

        amount = abs(int(row.get('amount') or 0))
        entry = rows.setdefault(int(user_id), {
            'user_id': int(user_id), 'spent': 0, 'spent_30': 0, 'gb': 0,
            'count': 0, 'first': at, 'last': at, 'gap': 0.0})
        entry['spent'] += amount
        entry['gb'] += int((row.get('meta') or {}).get('gb') or 0)
        entry['count'] += 1
        entry['first'] = min(entry['first'], at)
        entry['last'] = max(entry['last'], at)
        if at >= month_ago:
            entry['spent_30'] += amount

        gb = int((row.get('meta') or {}).get('gb') or 0)
        for window in sold:
            if at >= end - timedelta(days=window['days']):
                window['gb'] += gb
                window['money'] += amount
                window['purchases'] += 1
                window['people'].add(int(user_id))

    for entry in rows.values():
        if entry['count'] > 1:
            span = (entry['last'] - entry['first']).total_seconds() / 86400
            entry['gap'] = round(span / (entry['count'] - 1), 1)

    for window in sold:
        window['people'] = len(window['people'])
    return rows, sold


def _median(values: list) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def _buckets(rows: list[dict]) -> list[dict]:
    found = []
    for low, high in RATE_BUCKETS:
        inside = [row for row in rows
                  if row['rate'] >= low and (not high or row['rate'] < high)]
        found.append({'from': low, 'to': high, 'people': len(inside),
                      'money': sum(row['rate'] for row in inside)})
    return found


def _gone_buckets(rows: list[dict]) -> list[dict]:
    """Давно ли ушли — чтобы видеть, кого именно отсекает окно активности."""
    found = []
    for low, high in GONE_BUCKETS:
        inside = [row for row in rows
                  if row['days_since'] >= low
                  and (not high or row['days_since'] < high)]
        if inside:
            found.append({'from': low, 'to': high, 'people': len(inside)})
    return found
