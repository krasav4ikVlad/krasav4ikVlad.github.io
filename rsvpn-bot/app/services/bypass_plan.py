"""Под безлимитный ByPass: сколько каждый покупает и как часто.

Отличие от bypass_usage: там средние по всем, здесь — человек за человеком.
Цену безлимита нельзя поставить по среднему, потому что решение принимает
не средний человек, а каждый за себя: тот, кто сейчас тратит больше новой
цены, перейдёт (и вы потеряете разницу), тот, кто тратит меньше, —
останется на пакетах. Значит нужно распределение и перцентили, а не одно
число.

Считается на человека:

  * сколько гигабайт купил за период и на сколько рублей;
  * сколько раз докупал и с каким средним промежутком — привычка докупать
    и есть то, что безлимит заменяет;
  * сколько прокачал на самом деле (панель) — это расход, и при безлимите
    он вырастет, потому что счётчик перестанет мешать;
  * сколько платит за обычную подписку — чтобы видеть, кому безлимит
    продаётся вдобавок, а кому вместо.

Всё приводится к месяцу: отчёт зовут и за неделю, и за квартал.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import parse_dt
from app.services.bypass_usage import GB, _traffic

log = logging.getLogger(__name__)

FIELDS = {'user_data.user_id': 1, 'user_data.username': 1,
          'vpn.bypass_uuid': 1, 'vpn.bypass_trafficLimitBytes': 1}

# Что считать платой за обычную подписку: покупка и продление. Доп.
# устройства сюда не идут — это отдельная услуга, и к ByPass она отношения
# не имеет.
SUBSCRIPTION_KINDS = ('plan', 'renewal')

BUCKETS = ((0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 0))


async def collect(users, journal, panel, squad: str, start: datetime,
                  end: datetime) -> dict:
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
            'sub_paid': 0, 'used_bytes': 0,
            'left_bytes': int(users.pick(doc, 'vpn.bypass_trafficLimitBytes', 0) or 0),
        }

    await _fill_money(journal, people, start, end)
    traffic, panel_info = await _traffic(panel, squad, set(people), start, end)
    for user_id, used in traffic.items():
        people[user_id]['used_bytes'] = used

    days = max(1, (end - start).days)
    rows = list(people.values())
    for row in rows:
        row['gb_month'] = round(row['gb'] * 30 / days, 1)
        row['spent_month'] = round(row['spent'] * 30 / days)
        row['used_gb_month'] = round(row['used_bytes'] / GB * 30 / days, 1)
        row['sub_month'] = round(row['sub_paid'] * 30 / days)
        row['gap_days'] = _gap(row, days)

    rows.sort(key=lambda item: -item['spent_month'])
    buyers = [row for row in rows if row['purchases']]
    # Те, кто ничего не покупал, но качает: это бесплатный гигабайт при
    # подключении. На одного человека мелочь, на сто тысяч — основной расход,
    # и увидеть его можно только отдельной строкой.
    freeloaders = [row for row in rows if not row['purchases']
                   and row['used_gb_month'] > 0]

    return {
        'rows': rows, 'days': days, 'start': start, 'end': end,
        'buyers': buyers, 'panel': panel_info,
        'free_users': len(freeloaders),
        'free_gb_month': round(sum(row['used_gb_month'] for row in freeloaders), 1),
        'paid_gb_month': round(sum(row['used_gb_month'] for row in buyers), 1),
        'sold_gb_month': round(sum(row['gb_month'] for row in buyers), 1),
        'revenue_month': sum(row['spent_month'] for row in buyers),
    }


def economics(data: dict, cost_month: int) -> dict:
    """Сходится ли ByPass как бизнес и почём обходится гигабайт.

    Серверы оплачиваются помесячно и независимо от того, сколько по ним
    прокачали, поэтому себестоимость гигабайта здесь не задаётся, а
    выводится: расход за месяц поделить на прокачанное за месяц. Пока
    трафика мало, гигабайт дорогой; чем плотнее забиты те же серверы, тем
    он дешевле — и это главное, что нужно знать перед безлимитом.
    """
    real = data['free_gb_month'] + data['paid_gb_month']
    sold = data['sold_gb_month']

    return {
        'cost_month': int(cost_month),
        'revenue_month': data['revenue_month'],
        'profit': data['revenue_month'] - int(cost_month),
        'real_gb': round(real, 1),
        'cost_per_real_gb': round(cost_month / real, 2) if real else 0.0,
        # Сколько стоит гигабайт, который мы продаём. При коэффициенте 0.1
        # это десять настоящих, поэтому число получается в разы больше
        # цены пакета — и именно оно сравнивается с ценой.
        'cost_per_sold_gb': round(cost_month / sold, 2) if sold else 0.0,
        'price_per_sold_gb': round(data['revenue_month'] / sold, 2) if sold else 0.0,
        'free_share': round(100 * data['free_gb_month'] / real) if real else 0,
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


def simulate(rows: list[dict], prices: list[int], cost_per_gb: float = 0.0,
             growth: float = 2.0) -> list[dict]:
    """Что будет при безлимите по такой-то цене.

    Модель нарочно простая и пессимистичная к нам:

      * переходит тот, кому это выгодно, — у кого траты за месяц выше цены.
        Остальные остаются на пакетах и платят как платили;
      * трафик перешедших растёт: счётчик больше не мешает. Множитель
        задаётся, по умолчанию вдвое;
      * расход считается по настоящему трафику из панели, а не по
        оплаченному: платим мы за первое.

    Чего модель не знает — новых покупателей, которых безлимит приведёт.
    Поэтому её ответ это «не хуже чем», а не прогноз.
    """
    buyers = [row for row in rows if row['spent_month'] > 0]
    now_revenue = sum(row['spent_month'] for row in buyers)

    found = []
    for price in prices:
        switchers = [row for row in buyers if row['spent_month'] > price]
        stayers = [row for row in buyers if row['spent_month'] <= price]

        revenue = price * len(switchers) + sum(row['spent_month'] for row in stayers)
        traffic = sum(row['used_gb_month'] for row in switchers) * growth
        cost = round(traffic * cost_per_gb)

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
