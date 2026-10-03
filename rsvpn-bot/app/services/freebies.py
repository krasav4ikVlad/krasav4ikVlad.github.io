"""Кто живёт на подарках: три отчёта, каждый про свою дыру.

Подарков в боте несколько, и «абузят» их по-разному. Одни нельзя получить
дважды в принципе, другие утекают тихо и годами. Здесь считается то, что
видно по своей же базе, без обращений к панели.

  * **Запасной сервер (lifeline).** Подписка кончилась — человека переводят
    на служебный сквад, чтобы он мог открыть бота даже при блокировке
    Telegram. Если он не продлился, окно продлевается на следующем событии
    истечения — и так по кругу. Это и есть бесплатный сервер навсегда для
    любого, кто хоть раз платил.
  * **Один кошелёк на много аккаунтов.** Бонус новичку даётся один раз на
    аккаунт, а аккаунтов можно завести сколько угодно. Общий отпечаток
    оплаты — единственный след, который остаётся у бота.
  * **Бонус за возвращение.** Он-то как раз защищён: флаг кампании ставится
    навсегда. Вопрос к нему другой — окупается ли он вообще, и ответ
    считается здесь же: сколько роздано и сколько из получивших заплатили
    после этого.

Ни один из отчётов не выносит приговор. Один человек оплачивает подписку
жене и родителям — по данным это выглядит как накрутка, а на деле это
лучший клиент, какой бывает.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import now as time_now
from app.core.time import parse_dt
from app.domain.payments import fingerprint

log = logging.getLogger(__name__)

RIDER_FIELDS = {'user_data.user_id': 1, 'user_data.username': 1,
                'vpn.lifeline_at': 1, 'vpn.expireAt': 1,
                'growth.has_topup': 1, 'info.balance': 1}


async def lifeline_riders(users, over_days: int = 0,
                          now: datetime | None = None) -> dict:
    """Кто сидит на запасном сервере и сколько дней.

    Несколько дней — это то, ради чего lifeline и сделан: человек должен
    успеть открыть бота и продлить. Месяцы — это уже другое.
    """
    moment = now or time_now()
    rows = []

    async for doc in users.iterate({'vpn.in_lifeline': True}, RIDER_FIELDS):
        since = parse_dt(users.pick(doc, 'vpn.lifeline_at'))
        days = int((moment - since).total_seconds() // 86400) if since else 0
        rows.append({
            'user_id': users.pick(doc, 'user_data.user_id'),
            'username': users.pick(doc, 'user_data.username') or '',
            'days': days,
            'since': since,
            'paid_ever': bool(users.pick(doc, 'growth.has_topup')),
        })

    rows.sort(key=lambda row: -row['days'])
    over = [row for row in rows if over_days and row['days'] > over_days]
    return {
        'total': len(rows),
        'rows': rows,
        'over': len(over),
        'never_paid': len([row for row in rows if not row['paid_ever']]),
        'over_days': int(over_days),
    }


async def shared_payers(payments, min_accounts: int = 2,
                        limit: int = 20) -> list[dict]:
    """Один кошелёк — несколько аккаунтов. Группы, а не отдельные оплаты."""
    seen: dict[str, dict] = {}

    async for row in payments.iterate({'status': 'done'},
                                      {'user_id': 1, 'amount': 1,
                                       'payload': 1, 'created_at': 1}):
        print_ = fingerprint(row.get('payload'))
        user_id = row.get('user_id')
        if not print_ or not user_id:
            continue

        group = seen.setdefault(print_, {'print': print_, 'users': {},
                                         'amount': 0, 'payments': 0})
        group['users'][user_id] = group['users'].get(user_id, 0) + 1
        group['amount'] += int(row.get('amount') or 0)
        group['payments'] += 1

    groups = [{'print': item['print'], 'user_ids': sorted(item['users']),
               'accounts': len(item['users']), 'payments': item['payments'],
               'amount': item['amount']}
              for item in seen.values() if len(item['users']) >= min_accounts]

    groups.sort(key=lambda item: (-item['accounts'], -item['amount']))
    return groups[:limit]


async def return_bonus(balance_log, payments, start: datetime,
                       end: datetime) -> dict:
    """Сколько роздано бонусов за возвращение и сколько вернулось деньгами.

    «Вернулось» — это оплата ПОСЛЕ начисления, а не когда-нибудь: бонус,
    выданный тому, кто заплатил бы и так, ничего не купил.
    """
    given: dict[int, dict] = {}

    async for row in balance_log.iterate(
            {'kind': 'campaign', 'at': {'$gte': start, '$lte': end}},
            {'user_id': 1, 'amount': 1, 'at': 1}):
        user_id = row.get('user_id')
        at = parse_dt(row.get('at'))
        if not user_id or not at:
            continue
        entry = given.setdefault(user_id, {'amount': 0, 'first': at})
        entry['amount'] += int(row.get('amount') or 0)
        entry['first'] = min(entry['first'], at)

    returned, revenue = 0, 0
    for user_id, entry in given.items():
        paid = 0
        async for row in payments.iterate(
                {'user_id': user_id, 'status': 'done',
                 'created_at': {'$gte': entry['first']}},
                {'amount': 1}):
            paid += int(row.get('amount') or 0)
        if paid:
            returned += 1
            revenue += paid

    return {
        'people': len(given),
        'given': sum(entry['amount'] for entry in given.values()),
        'returned': returned,
        'revenue': revenue,
        'start': start, 'end': end,
    }
