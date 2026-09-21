"""Билеты розыгрыша: считаются по покупкам подписок, а не по пополнениям.

Источников билета два, и оба про подписку, а не про деньги на балансе:

  * **новый приглашённый друг** купил подписку от месяца — участнику три
    билета. Три, а не один: привести человека, который заплатит, тяжелее,
    чем продлиться самому, и разница должна быть видна. За второго и
    третьего друга — ещё по три; за повторные покупки того же друга не
    даётся ничего, иначе билеты набирались бы на одном и том же человеке;
  * **своя покупка** — билет за каждый месяц. Купил полгода — шесть
    билетов. Это то, что превращает акцию из «приведи друзей» в «продлись
    сейчас», а вторых у нас гораздо больше.

Считается всё по журналу баланса: покупка и продление подписки лежат там
с кодами `plan` и `renewal` и с длиной в `meta.days`. Пополнение баланса
билетов не даёт вовсе — деньги на балансе это ещё не подписка.

Проход по журналу полный, потому что «первая в жизни покупка» иначе не
определяется: нужно знать, покупал ли друг когда-нибудь раньше.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import now as time_now
from app.core.time import parse_dt
from app.domain import raffle as domain

log = logging.getLogger(__name__)

# Покупка и продление подписки. Пополнение баланса (`topup`) сюда не идёт:
# деньги на балансе — ещё не подписка, и билет за них давать не за что.
KINDS = ('plan', 'renewal')

JOURNAL_FIELDS = {'user_id': 1, 'amount': 1, 'kind': 1, 'meta': 1, 'at': 1}
USER_FIELDS = {'user_data.user_id': 1, 'user_data.username': 1,
               'user_data.referrer': 1, 'vpn.uuid': 1, 'vpn.expireAt': 1}
CHUNK = 500
DAYS_IN_MONTH = 30


def months(days) -> int:
    """Сколько месяцев в покупке. Меньше месяца — ноль, а не «почти месяц»."""
    try:
        value = int(days or 0)
    except (TypeError, ValueError):
        return 0
    return value // DAYS_IN_MONTH


async def collect(journal, users, *, start: datetime, end: datetime,
                  friend_tickets: int = 3, self_per_month: int = 1,
                  min_months: int = 1, require_active: bool = True,
                  now: datetime | None = None) -> dict:
    """Все билеты за период плюс то, что в зачёт не пошло, и почему."""
    moment = now or time_now()
    first, inside = await _purchases(journal, start, end)

    buyers = list(inside)
    docs = await _docs(users, buyers)
    referrer_ids = {users.pick(doc, 'user_data.referrer')
                    for doc in docs.values()
                    if users.pick(doc, 'user_data.referrer')}
    referrers = await _docs(users, [int(ref) for ref in referrer_ids
                                    if str(ref).lstrip('-').isdigit()])

    events: list[dict] = []
    for user_id in buyers:
        purchases = inside[user_id]
        doc = docs.get(user_id) or {}

        # ── свои покупки: билет за каждый месяц
        own = sum(months(row['days']) for row in purchases) * int(self_per_month)
        if own > 0:
            events.append({
                'kind': domain.SELF, 'owner': user_id,
                'owner_username': users.pick(doc, 'user_data.username') or '',
                'friend': 0, 'friend_username': '',
                'at': min(row['at'] for row in purchases),
                'tickets': own, 'valid': True, 'why': '',
                'detail': f'своя подписка, {sum(months(row["days"]) for row in purchases)} мес.',
            })

        # ── он же как приглашённый друг: три билета тому, кто его привёл
        events.append(_friend_event(
            users, user_id, doc, purchases, first.get(user_id), referrers,
            start=start, end=end, friend_tickets=friend_tickets,
            min_months=min_months, require_active=require_active,
            moment=moment))

    events = [row for row in events if row]
    tickets = domain.number_tickets(events)

    return {
        'start': start, 'end': end,
        'friend_tickets': int(friend_tickets),
        'self_per_month': int(self_per_month),
        'min_months': int(min_months),
        'require_active': bool(require_active),
        'events': events,
        'rows': tickets,
        'tickets': len(tickets),
        'revenue': sum(row['amount'] for group in inside.values() for row in group),
        'participants': _participants(events),
        'skipped': _skipped(events),
    }


def _friend_event(users, user_id: int, doc: dict, purchases: list[dict],
                  first_ever, referrers: dict, *, start, end, friend_tickets,
                  min_months, require_active, moment) -> dict | None:
    """Билеты тому, кто привёл этого человека, — если он новый и купил месяц."""
    referrer = users.pick(doc, 'user_data.referrer')
    referrer = int(referrer) if str(referrer or '').lstrip('-').isdigit() else None

    row = {
        'kind': domain.FRIEND, 'owner': referrer or 0,
        'owner_username': (users.pick(referrers.get(referrer) or {},
                                      'user_data.username') or ''
                           if referrer else ''),
        'friend': user_id,
        'friend_username': users.pick(doc, 'user_data.username') or '',
        'at': min(item['at'] for item in purchases),
        'tickets': int(friend_tickets), 'valid': False, 'why': '',
        'detail': '',
    }
    bought = max(months(item['days']) for item in purchases)
    row['detail'] = f'подписка {bought} мес.'

    if not referrer:
        row['why'] = domain.NO_REFERRER
    elif referrer == user_id:
        row['why'] = domain.SELF_INVITE
    elif referrer not in referrers:
        row['why'] = domain.UNKNOWN_REFERRER
    elif bought < int(min_months):
        row['why'] = domain.TOO_SHORT
    elif not first_ever or not (start <= first_ever <= end):
        # Друг покупал и раньше — он не новый, и билетов за него уже не дают.
        row['why'] = domain.NOT_NEW
    elif require_active and not _alive(users, doc, moment):
        row['why'] = domain.NOT_ACTIVE
    else:
        row['valid'] = True

    return row


async def _purchases(journal, start: datetime,
                     end: datetime) -> tuple[dict, dict]:
    """Первая в жизни покупка подписки и всё, что куплено за период."""
    first: dict[int, datetime] = {}
    inside: dict[int, list[dict]] = {}

    async for row in journal.iterate({'kind': {'$in': list(KINDS)}},
                                     JOURNAL_FIELDS):
        user_id = row.get('user_id')
        at = parse_dt(row.get('at'))
        if user_id is None or at is None:
            continue

        user_id = int(user_id)
        days = (row.get('meta') or {}).get('days')
        if months(days) <= 0:
            # Покупка короче месяца (тестовый тариф, доплата) — не покупка
            # подписки в смысле акции, и «первой» она тоже не считается.
            continue

        seen = first.get(user_id)
        if seen is None or at < seen:
            first[user_id] = at
        if start <= at <= end:
            inside.setdefault(user_id, []).append(
                {'at': at, 'days': days,
                 'amount': abs(int(row.get('amount') or 0))})

    return first, inside


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


def _alive(users, doc: dict, moment: datetime) -> bool:
    expires = parse_dt(users.pick(doc, 'vpn.expireAt'))
    return bool(expires and expires > moment)


def _participants(events: list[dict]) -> list[dict]:
    """Участники с их билетами — от большего к меньшему."""
    counts: dict[int, dict] = {}
    for row in events:
        if not row['valid'] or not row['owner']:
            continue
        entry = counts.setdefault(row['owner'],
                                  {'user_id': row['owner'],
                                   'username': row['owner_username'],
                                   'tickets': 0, 'friends': 0, 'own': 0})
        entry['tickets'] += row['tickets']
        if row['kind'] == domain.FRIEND:
            entry['friends'] += 1
        else:
            entry['own'] += row['tickets']

    return sorted(counts.values(), key=lambda item: (-item['tickets'],
                                                     item['user_id']))


def _skipped(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in events:
        if not row['valid']:
            counts[row['why']] = counts.get(row['why'], 0) + 1
    return counts


# ── билеты одного человека ──────────────────────────────────────────────────
#
# Экран в боте не может позволить себе проход по всему журналу: его
# открывают тысячи людей. Поэтому для одного участника считаем иначе —
# сначала его друзья, потом покупки его и их, — но по тем же правилам, что
# и таблица для розыгрыша. Иначе бот показывал бы одно число, а в списке
# билетов стояло другое, и правым был бы человек.

MAX_FRIENDS = 1000
MINE_FIELDS = {'user_data.user_id': 1, 'vpn.expireAt': 1}


async def for_user(journal, users, user_id: int, *, start: datetime,
                   end: datetime, friend_tickets: int = 3,
                   self_per_month: int = 1, min_months: int = 1,
                   require_active: bool = True,
                   now: datetime | None = None) -> dict:
    """Сколько билетов у этого человека и из чего они сложились."""
    moment = now or time_now()

    friends: dict[int, dict] = {}
    cursor = users.col.find({'user_data.referrer': int(user_id)}, MINE_FIELDS)
    async for doc in cursor:
        friend_id = users.pick(doc, 'user_data.user_id')
        if friend_id is None or int(friend_id) == int(user_id):
            continue
        friends[int(friend_id)] = doc
        if len(friends) >= MAX_FRIENDS:
            break

    first: dict[int, datetime] = {}
    inside: dict[int, list[dict]] = {}
    async for row in journal.iterate(
            {'kind': {'$in': list(KINDS)},
             'user_id': {'$in': [int(user_id), *friends]}},
            JOURNAL_FIELDS):
        owner = row.get('user_id')
        at = parse_dt(row.get('at'))
        days = (row.get('meta') or {}).get('days')
        if owner is None or at is None or months(days) <= 0:
            continue
        owner = int(owner)
        seen = first.get(owner)
        if seen is None or at < seen:
            first[owner] = at
        if start <= at <= end:
            inside.setdefault(owner, []).append({'at': at, 'days': days})

    own = sum(months(row['days']) for row in inside.get(int(user_id), []))
    own_tickets = own * int(self_per_month)

    good_friends = 0
    for friend_id, doc in friends.items():
        purchases = inside.get(friend_id)
        if not purchases:
            continue
        if max(months(row['days']) for row in purchases) < int(min_months):
            continue
        came = first.get(friend_id)
        if not came or not (start <= came <= end):
            continue
        if require_active and not _alive(users, doc, moment):
            continue
        good_friends += 1

    return {
        'tickets': own_tickets + good_friends * int(friend_tickets),
        'own_months': own,
        'own_tickets': own_tickets,
        'friends': good_friends,
        'friend_tickets': good_friends * int(friend_tickets),
        'invited': len(friends),
    }
