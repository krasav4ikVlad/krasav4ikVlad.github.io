"""Под безлимитный ByPass: сколько каждый покупает и как часто.

Отличие от bypass_arpu: там одна цифра — средняя ставка, здесь человек за
человеком и распределение. Цену безлимита нельзя поставить по среднему,
потому что решение принимает не средний человек, а каждый за себя: тот,
кто сейчас тратит больше новой цены, перейдёт (и вы потеряете разницу),
тот, кто тратит меньше, — останется на пакетах.

**Панель не спрашивается.** Сначала расход брался у неё, но ByPass-подписки
она отдаёт под своими внутренними номерами, а не под telegram id, и свести
их не с чем. Да и незачем: гигабайты здесь покупают впрок и тратят до
лимита, а потом докупают — значит проданное и есть прокачанное, с задержкой
в несколько дней. Всё считается по журналу списаний.

Настоящий трафик выводится из проданного через коэффициент списания: при
0.1 за каждый проданный гигабайт в канал уходит десять. Это и есть разница
между «продали на 528 тысяч» и «серверы забиты под завязку».
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import parse_dt

log = logging.getLogger(__name__)

GB = 1024 ** 3

FIELDS = {'user_data.user_id': 1, 'user_data.username': 1,
          'vpn.bypass_uuid': 1, 'vpn.bypass_trafficLimitBytes': 1}

# Что считать платой за обычную подписку: покупка и продление. Доп.
# устройства сюда не идут — это отдельная услуга, и к ByPass она отношения
# не имеет.
SUBSCRIPTION_KINDS = ('plan', 'renewal')

BUCKETS = ((0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 0))


async def collect(users, journal, start: datetime, end: datetime) -> dict:
    """Строка на каждого, у кого подключён ByPass."""
    people: dict[int, dict] = {}

    async for doc in users.iterate({'vpn.bypass_uuid': {'$nin': ['', None]}},
                                   FIELDS):
        user_id = users.pick(doc, 'user_data.user_id')
        if user_id is None:
            continue
        people[int(user_id)] = {
            'user_id': int(user_id),
            'username': users.pick(doc, 'user_data.username') or '',
            'gb': 0, 'spent': 0, 'purchases': 0,
            'first': None, 'last': None,
            'sub_paid': 0,
            'limit_bytes': int(users.pick(doc, 'vpn.bypass_trafficLimitBytes', 0) or 0),
        }

    await _fill_money(journal, people, start, end)

    days = max(1, (end - start).days)
    rows = list(people.values())
    for row in rows:
        row['gb_month'] = round(row['gb'] * 30 / days, 1)
        row['spent_month'] = round(row['spent'] * 30 / days)
        row['sub_month'] = round(row['sub_paid'] * 30 / days)
        row['gap_days'] = _gap(row, days)

    rows.sort(key=lambda item: -item['spent_month'])
    buyers = [row for row in rows if row['purchases']]

    return {
        'rows': rows, 'days': days, 'start': start, 'end': end,
        'buyers': buyers,
        'sold_gb_month': round(sum(row['gb_month'] for row in buyers), 1),
        'revenue_month': sum(row['spent_month'] for row in buyers),
        'sub_month': sum(row['sub_month'] for row in buyers),
        'sub_payers': len([row for row in buyers if row['sub_month']]),
        'limit_gb': round(sum(row['limit_bytes'] for row in rows) / GB, 1),
    }


def economics(data: dict, cost_month: int, rate: float = 0.1) -> dict:
    """Сходится ли ByPass и почём обходится гигабайт.

    Себестоимость гигабайта не спрашивается, а выводится: серверы
    оплачиваются помесячно и независимо от прокачанного, поэтому цена
    гигабайта — это плата за месяц, делённая на объём. Чем плотнее забиты
    те же серверы, тем гигабайт дешевле.

    Считаются оба гигабайта, и путать их дорого: настоящий — тот, что
    уходит в канал, проданный — тот, за который платят. При коэффициенте
    0.1 второй в десять раз крупнее, и сравнивать с ценой пакета надо
    именно его.
    """
    sold = data['sold_gb_month']
    rate = float(rate) if rate and rate > 0 else 1.0
    real = round(sold / rate, 1)

    return {
        'cost_month': int(cost_month),
        'revenue_month': data['revenue_month'],
        'profit': data['revenue_month'] - int(cost_month),
        'rate': rate,
        'sold_gb': sold,
        'real_gb': real,
        'cost_per_real_gb': round(cost_month / real, 2) if real else 0.0,
        'cost_per_sold_gb': round(cost_month / sold, 2) if sold else 0.0,
        'price_per_sold_gb': round(data['revenue_month'] / sold, 2) if sold else 0.0,
    }


async def _fill_money(journal, people: dict[int, dict], start: datetime,
                      end: datetime) -> None:
    """Покупки трафика и плата за обычную подписку — одним проходом."""
    async for row in journal.iterate(
            {'kind': {'$in': ['bypass', *SUBSCRIPTION_KINDS]},
             'at': {'$gte': start, '$lte': end}},
            {'user_id': 1, 'amount': 1, 'kind': 1, 'meta': 1, 'at': 1}):
        person = people.get(int(row.get('user_id') or 0))
        if person is None:
            continue

        amount = abs(int(row.get('amount') or 0))
        if row.get('kind') != 'bypass':
            person['sub_paid'] += amount
            continue

        at = parse_dt(row.get('at'))
        person['spent'] += amount
        person['gb'] += int((row.get('meta') or {}).get('gb') or 0)
        person['purchases'] += 1
        if at:
            person['first'] = min(person['first'] or at, at)
            person['last'] = max(person['last'] or at, at)


def _gap(row: dict, days: int) -> float:
    """Средний промежуток между докупками, в днях.

    Одна покупка промежутка не даёт — там «не знаем», а не «ноль»: человек
    мог купить в последний день периода и докупить завтра.
    """
    if row['purchases'] < 2 or not row['first'] or not row['last']:
        return 0.0
    span = (row['last'] - row['first']).total_seconds() / 86400
    return round(span / (row['purchases'] - 1), 1)


def buckets(rows: list[dict]) -> list[dict]:
    """Распределение по гигабайтам в месяц — по нему и ставят цену."""
    found = []
    for low, high in BUCKETS:
        inside = [row for row in rows
                  if row['gb_month'] >= low and (not high or row['gb_month'] < high)]
        found.append({'from': low, 'to': high, 'people': len(inside),
                      'spent': sum(row['spent_month'] for row in inside)})
    return found


def percentile(values: list[float], share: int) -> float:
    """Перцентиль «по ближайшему»: точности хватает, а гадать не приходится."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share / 100 * len(ordered)) - 1))
    return ordered[index]


def simulate(rows: list[dict], prices: list[int], cost_per_real_gb: float = 0.0,
             rate: float = 0.1, growth: float = 2.0) -> list[dict]:
    """Что будет при безлимите по такой-то цене.

    Модель нарочно простая и пессимистичная к нам:

      * переходит тот, кому это выгодно, — у кого траты за месяц выше цены.
        Остальные остаются на пакетах и платят как платили;
      * трафик перешедших растёт: счётчик больше не мешает. Множитель
        задаётся, по умолчанию вдвое;
      * расход считается по настоящему трафику — проданное, делённое на
        коэффициент: платим мы за то, что ушло в канал, а не за то, что
        списали с лимита.

    Чего модель не знает — новых покупателей, которых безлимит приведёт.
    Поэтому её ответ это «не хуже чем», а не прогноз.
    """
    buyers = [row for row in rows if row['spent_month'] > 0]
    now_revenue = sum(row['spent_month'] for row in buyers)
    rate = float(rate) if rate and rate > 0 else 1.0

    found = []
    for price in prices:
        switchers = [row for row in buyers if row['spent_month'] > price]
        stayers = [row for row in buyers if row['spent_month'] <= price]

        revenue = price * len(switchers) + sum(row['spent_month'] for row in stayers)
        traffic = sum(row['gb_month'] for row in switchers) / rate * growth
        cost = round(traffic * cost_per_real_gb)

        found.append({
            'price': price,
            'switchers': len(switchers),
            'revenue': round(revenue),
            'delta': round(revenue - now_revenue),
            'traffic_gb': round(traffic, 1),
            'cost': cost,
            'profit': round(revenue - cost),
        })
    return found
