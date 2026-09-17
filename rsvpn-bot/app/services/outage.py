"""Кто приходил в бота, пока он молчал.

Когда бот лежит, он ничего не записывает — это и есть сложность. Но
Telegram хранит недоставленные обновления около суток и отдаёт их сразу
после подъёма, а бот запускается с `drop_pending_updates=False`, то есть
разбирает всю накопившуюся ночь. В журнале действий эти обращения
появляются — с временем разбора, а не нажатия.

Отсюда два разных окна, и путать их нельзя:

  * **бот лежал** — обращений в журнале нет вовсе, зато сразу после
    подъёма видна пачка: это те, кто писал ночью;
  * **бот отвечал, но ломался** — обращения есть с настоящим временем, а
    рядом с ними ошибки в журнале ошибок.

Команда просто показывает, кто попал в заданное окно, и не решает за
человека, какое окно правильное: это видно по отчёту.

Отдельно считаются оплаты за то же время. Если вместе с ботом лежал и API,
деньги могли уйти провайдеру и не дойти до баланса — это куда хуже
неотвеченного нажатия, и узнать об этом надо в ту же минуту.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.core.time import parse_dt
from app.repositories.users import UsersRepository

log = logging.getLogger(__name__)

# Сколько последних записей журнала смотрим у каждого. Ночь обращений — это
# единицы записей; сотня с запасом закрывает и самых активных, а тянуть все
# 350 из каждого документа — лишние мегабайты на ровном месте.
LOG_TAIL = 100

FIELDS = {
    'user_data.user_id': 1, 'user_data.username': 1, 'vpn.expireAt': 1,
    UsersRepository.BLOCKED: 1,
    'logs': {'$slice': -LOG_TAIL},
}

DAY = '%d.%m.%Y'


def days_between(start: datetime, end: datetime) -> list[str]:
    """Даты окна как строки журнала — по ним отсеиваем на стороне Mongo.

    Время в журнале хранится строкой «17.09.2026 03:12:44», сравнивать её
    как дату нельзя, а вот начало строки — можно: дата там фиксированной
    ширины. Точное время дофильтровывается уже в Python.
    """
    days, current = [], start.date()
    while current <= end.date():
        days.append(current.strftime(DAY))
        current += timedelta(days=1)
    return days


def query_for(start: datetime, end: datetime) -> dict:
    escaped = '|'.join(day.replace('.', r'\.') for day in days_between(start, end))
    return {'logs.timestamp': {'$regex': f'^({escaped})'}}


async def visitors(users, start: datetime, end: datetime,
                   limit: int = 20000) -> dict:
    """Кто обращался в окне: id, сколько раз и когда в первый и последний."""
    people: list[dict] = []
    blocked = 0
    stopped = False

    async for doc in users.iterate(query_for(start, end), FIELDS):
        hits = [stamp for entry in (doc.get('logs') or [])
                if (stamp := parse_dt(entry.get('timestamp')))
                and start <= stamp <= end]
        if not hits:
            continue

        if users.pick(doc, UsersRepository.BLOCKED):
            # Заблокировавшему бота письмо не дойдёт: считаем отдельно, в
            # получатели не берём. Иначе «отправлено 400, доставлено 280»
            # выглядело бы как сбой рассылки.
            blocked += 1
            continue

        people.append({
            'user_id': users.pick(doc, 'user_data.user_id'),
            'username': users.pick(doc, 'user_data.username') or '',
            'hits': len(hits),
            'first': min(hits),
            'last': max(hits),
            'active': _active(users, doc, end),
        })
        if len(people) >= limit:
            stopped = True
            break

    people.sort(key=lambda item: (-item['hits'], item['user_id']))
    return {'people': people, 'blocked': blocked, 'stopped': stopped,
            'total': len(people)}


def _active(users, doc: dict, moment: datetime) -> bool:
    expires = parse_dt(users.pick(doc, 'vpn.expireAt'))
    return bool(expires and expires > moment)


async def payments_in(payments, start: datetime, end: datetime) -> dict:
    """Оплаты за то же окно: сколько дошло и что зависло.

    Зависшая оплата — это не строка в отчёте, а человек, у которого списали
    деньги и ничего не зачислили. Поэтому такие показываются поимённо, а не
    числом.
    """
    done, stuck = 0, []
    total = 0

    async for row in payments.iterate(
            {'created_at': {'$gte': start, '$lte': end}},
            {'txid': 1, 'user_id': 1, 'amount': 1, 'status': 1,
             'provider': 1, 'created_at': 1}):
        total += 1
        if row.get('status') == 'done':
            done += 1
            continue
        stuck.append({'txid': row.get('txid'), 'user_id': row.get('user_id'),
                      'amount': int(row.get('amount') or 0),
                      'status': row.get('status'),
                      'provider': row.get('provider') or '',
                      'at': parse_dt(row.get('created_at'))})

    stuck.sort(key=lambda item: item['at'] or start)
    return {'total': total, 'done': done, 'stuck': stuck,
            'lost': sum(item['amount'] for item in stuck)}


async def report(users, payments, start: datetime, end: datetime,
                 limit: int = 20000) -> dict:
    data = await visitors(users, start, end, limit=limit)
    data['payments'] = await payments_in(payments, start, end)
    data['start'], data['end'] = start, end
    return data
