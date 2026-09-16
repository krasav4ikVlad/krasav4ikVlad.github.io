"""Билеты розыгрыша: считаются по платежам, а не по счётчикам.

Счётчик приглашённых в документе человека для розыгрыша не годится: он
пожизненный и ничего не знает о периоде акции. Билеты считаются от самих
платежей — это единственные настоящие деньги в системе, и у каждого есть
дата.

Проход один и полный: нужна первая в жизни оплата каждого плательщика, а
«первую» нельзя узнать, глядя только внутрь периода. Поэтому команда
считает всю коллекцию платежей — это медленно, зато не врёт.

Отсюда же берётся таблица для розыгрыша: билеты выгружаются как есть, с
датами и пометками подозрений, и победителей выбирают по ней. Отказавшиеся
оплаты из таблицы не выбрасываются, а помечаются причиной — иначе на вопрос
«а почему у меня не засчиталось» отвечать будет нечем.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import now as time_now
from app.core.time import parse_dt
from app.domain import raffle as domain

log = logging.getLogger(__name__)

PAYMENT_FIELDS = {'user_id': 1, 'amount': 1, 'created_at': 1, 'payload': 1,
                  'status': 1}
USER_FIELDS = {'user_data.user_id': 1, 'user_data.username': 1,
               'user_data.referrer': 1, 'user_data.date_joined': 1,
               'vpn.uuid': 1, 'vpn.expireAt': 1}
CHUNK = 500


async def collect(payments, users, *, start: datetime, end: datetime,
                  min_payment: int = 0, require_active: bool = True,
                  now: datetime | None = None) -> dict:
    """Все билеты за период плюс то, что в зачёт не пошло, и почему."""
    moment = now or time_now()
    first, in_window = await _payments(payments, start, end)

    # Новые плательщики: те, чья первая в жизни оплата попала в период.
    # Старый плательщик, оплативший ещё раз, билета не даёт — за продления
    # своих же друзей билеты не выдаём.
    newcomers = [user_id for user_id, record in first.items()
                 if start <= record['at'] <= end]

    friends = await _docs(users, newcomers)
    referrer_ids = {users.pick(doc, 'user_data.referrer')
                    for doc in friends.values()
                    if users.pick(doc, 'user_data.referrer')}
    referrers = await _docs(users, [int(ref) for ref in referrer_ids
                                    if str(ref).lstrip('-').isdigit()])

    rows = []
    for user_id in newcomers:
        record = first[user_id]
        doc = friends.get(user_id) or {}
        referrer = users.pick(doc, 'user_data.referrer')
        referrer = int(referrer) if str(referrer or '').lstrip('-').isdigit() else None
        paid = int(in_window.get(user_id, 0))

        row = {
            'ticket': 0,
            'at': record['at'],
            'friend_id': user_id,
            'friend_username': users.pick(doc, 'user_data.username') or '',
            'friend_joined': parse_dt(users.pick(doc, 'user_data.date_joined')),
            'amount': paid,
            'referrer_id': referrer,
            'referrer_username': '',
            'print': record['print'],
            'valid': False,
            'why': '',
            'flags': [],
        }

        if not referrer:
            row['why'] = domain.NO_REFERRER
        elif referrer == user_id:
            row['why'] = domain.SELF_INVITE
        elif referrer not in referrers:
            row['why'] = domain.UNKNOWN_REFERRER
        elif paid < int(min_payment):
            row['why'] = domain.TOO_SMALL
        elif require_active and not _active(users, doc, moment):
            row['why'] = domain.NOT_ACTIVE
        else:
            row['valid'] = True

        if referrer in referrers:
            row['referrer_username'] = users.pick(
                referrers[referrer], 'user_data.username') or ''
        if row['valid'] and not users.pick(doc, 'vpn.uuid'):
            # Заплатил, но подписки в панели нет — значит и не подключался.
            domain.add_flag(row, domain.NEVER_CONNECTED)

        rows.append(row)

    good = [row for row in rows if row['valid']]
    domain.mark_batches(good)
    domain.mark_same_payer(good)
    domain.number(rows)

    return {
        'start': start, 'end': end, 'min_payment': int(min_payment),
        'require_active': bool(require_active),
        'rows': rows,
        'tickets': len(good),
        'revenue': sum(row['amount'] for row in good),
        'participants': _participants(good),
        'flagged': len([row for row in good if row['flags']]),
        'skipped': _skipped(rows),
        'payers': len(first),
    }


async def _payments(payments, start: datetime, end: datetime) -> tuple[dict, dict]:
    """Первая в жизни оплата каждого плательщика и сумма его оплат за период."""
    first: dict[int, dict] = {}
    in_window: dict[int, int] = {}

    async for row in payments.iterate({'status': 'done'}, PAYMENT_FIELDS):
        user_id = row.get('user_id')
        moment = parse_dt(row.get('created_at'))
        if not user_id or not moment:
            continue

        amount = int(row.get('amount') or 0)
        seen = first.get(user_id)
        if seen is None or moment < seen['at']:
            first[user_id] = {'at': moment, 'amount': amount,
                              'print': domain.fingerprint(row.get('payload'))}
        if start <= moment <= end:
            in_window[user_id] = in_window.get(user_id, 0) + amount

    return first, in_window


async def _docs(users, ids: list[int]) -> dict[int, dict]:
    """Документы по списку id — порциями: один $in на десять тысяч Mongo не любит."""
    found: dict[int, dict] = {}
    unique = list(dict.fromkeys(ids))

    for begin in range(0, len(unique), CHUNK):
        chunk = unique[begin:begin + CHUNK]
        cursor = users.col.find({'user_data.user_id': {'$in': chunk}}, USER_FIELDS)
        async for doc in cursor:
            user_id = users.pick(doc, 'user_data.user_id')
            if user_id is not None:
                found[int(user_id)] = doc
    return found


def _active(users, doc: dict, moment: datetime) -> bool:
    expires = parse_dt(users.pick(doc, 'vpn.expireAt'))
    return bool(expires and expires > moment)


def _participants(rows: list[dict]) -> list[dict]:
    """Участники с их числом билетов — от большего к меньшему."""
    counts: dict[int, dict] = {}
    for row in rows:
        entry = counts.setdefault(row['referrer_id'],
                                  {'user_id': row['referrer_id'],
                                   'username': row['referrer_username'],
                                   'tickets': 0, 'amount': 0})
        entry['tickets'] += 1
        entry['amount'] += row['amount']

    return sorted(counts.values(), key=lambda item: (-item['tickets'], item['user_id']))


def _skipped(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not row['valid']:
            counts[row['why']] = counts.get(row['why'], 0) + 1
    return counts
