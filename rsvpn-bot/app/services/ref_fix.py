"""Починка потерянных рефералов: метки не было, люди по ней приходили.

Случай, ради которого написано. У блогера была именная ссылка
`?start=ref_RepublickCheck`, зашитая в старом боте. При переходе на новый
она осталась только в тексте ссылки: метки в базе нет, алиаса в настройках
нет — и `resolve_referrer` честно возвращает «пригласившего нет». Человек
регистрируется, в его карточке `referrer` пустой, и все его будущие
пополнения идут мимо блогера. Полтора месяца и три тысячи человек.

Сама ссылка при этом работала: Telegram передавал `ref_RepublickCheck`
в `/start`, и бот сохранял её целиком в `user_data.utm`. То есть все
потерянные видны поимённо — по ним и чиним.

Что делает починка:

  * проставляет `referrer` и `ref_tag` тем, у кого их нет;
  * добавляет их в список рефералов владельца метки;
  * доначисляет процент с их прошлых пополнений — тех, по которым он
    ничего не получил.

Чего не делает: не трогает тех, у кого referrer уже стоит (там всё
посчитано), и не начисляет дважды — на каждого чинёного ставится отметка.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.time import now, parse_dt

log = logging.getLogger(__name__)

# Отметка о починке — в карточке приведённого, а не в сводке: сводку
# потеряют, а карточка переживёт и перезапуск, и повторный вызов команды.
MARK = 'user_data.ref_fixed_at'

CHUNK = 500


def start_payloads(tag: str) -> list[str]:
    """Как эта метка могла выглядеть в /start. Регистр не важен: люди
    копируют ссылку как придётся, а Telegram отдаёт её как есть."""
    clean = (tag or '').strip().lstrip('@')
    return [f'ref_{clean}', clean]


async def find(users, journal, *, tag: str, since: datetime,
               owner_id: int) -> dict:
    """Кого потеряли и сколько это стоило. Ничего не меняет."""
    payloads = [item.lower() for item in start_payloads(tag)]

    lost: list[dict] = []
    already: int = 0
    before: int = 0
    variants: dict[str, int] = {}
    needle = (tag or '').strip().lower()

    cursor = users.col.find(
        {'user_data.utm': {'$exists': True}},
        {'user_data.user_id': 1, 'user_data.username': 1, 'user_data.utm': 1,
         'user_data.referrer': 1, 'user_data.date_joined': 1,
         'user_data.ref_fixed_at': 1})

    async for doc in cursor:
        utm = str(users.pick(doc, 'user_data.utm') or '').strip().lower()
        if utm not in payloads:
            # Похожая, но не та же ссылка: `ref_метка_yt`, `метка2`,
            # опечатка в раздаче. Их не чиним вслепую, но показываем —
            # иначе «нашлось мало» не с чем сверить.
            if needle and needle in utm:
                variants[utm] = variants.get(utm, 0) + 1
            continue

        joined = parse_dt(users.pick(doc, 'user_data.date_joined'))
        if joined and joined < since:
            before += 1
            continue

        user_id = users.pick(doc, 'user_data.user_id')
        if user_id is None:
            continue

        if users.pick(doc, 'user_data.referrer'):
            already += 1
            continue

        lost.append({
            'user_id': int(user_id),
            'username': users.pick(doc, 'user_data.username') or '',
            'at': joined,
            'fixed_at': users.pick(doc, 'user_data.ref_fixed_at'),
        })

    paid = await _paid_by(journal, [row['user_id'] for row in lost])
    for row in lost:
        row['paid'] = paid.get(row['user_id'], 0)

    fresh = [row for row in lost if not row['fixed_at']]
    payers = [row for row in fresh if row['paid']]
    return {
        'tag': tag, 'since': since, 'owner_id': int(owner_id),
        'found': lost,
        'fresh': fresh,
        'already': already,
        'before': before,
        'variants': sorted(variants.items(), key=lambda pair: -pair[1]),
        'paid': sum(row['paid'] for row in payers),
        'payers': len(payers),
        'average': round(sum(row['paid'] for row in payers) / len(payers))
                   if payers else 0,
        'months': by_month(fresh),
    }


def by_month(rows: list[dict]) -> list[tuple[str, int]]:
    """Потери по месяцам — чтобы увидеть, когда всё началось на самом деле.

    Дата поломки берётся из головы («примерно с восьмого»), и если потери
    тянутся с более раннего месяца, это видно только так.
    """
    counts: dict[str, int] = {}
    for row in rows:
        at = row.get('at')
        if at is None:
            continue
        counts[at.strftime('%m.%Y')] = counts.get(at.strftime('%m.%Y'), 0) + 1
    return sorted(counts.items())


async def _paid_by(journal, user_ids: list[int]) -> dict[int, int]:
    """Сколько каждый из них внёс деньгами.

    Процент считается от оплаченного, а не от зачисленного: бонус за
    пополнение — наш подарок, и платить с него процент не за что. В журнале
    оплаченное лежит в meta.paid, и только если его нет — берём сумму.
    """
    totals: dict[int, int] = {}
    unique = list(dict.fromkeys(user_ids))

    for begin in range(0, len(unique), CHUNK):
        chunk = unique[begin:begin + CHUNK]
        cursor = journal.col.find(
            {'kind': 'topup', 'user_id': {'$in': chunk}},
            {'user_id': 1, 'amount': 1, 'meta': 1})
        async for row in cursor:
            user_id = int(row.get('user_id') or 0)
            meta = row.get('meta') or {}
            amount = int(meta.get('paid') or row.get('amount') or 0)
            if amount > 0:
                totals[user_id] = totals.get(user_id, 0) + amount
    return totals


async def repair(users, data: dict, *, rate: float, admin_id: int = 0) -> dict:
    """Проставить связь и доначислить процент. Возвращает, что вышло."""
    owner_id = int(data['owner_id'])
    tag = data['tag']
    report = {'linked': 0, 'reward': 0, 'payers': 0}

    for row in data['fresh']:
        user_id = row['user_id']
        reward = int(row['paid'] * rate)

        linked = await users.col.update_one(
            # Условие — часть защиты от двойного начисления: если между
            # показом и починкой связь появилась, второй раз не ставим.
            {'user_data.user_id': user_id,
             'user_data.ref_fixed_at': {'$exists': False},
             '$or': [{'user_data.referrer': ''},
                     {'user_data.referrer': {'$exists': False}},
                     {'user_data.referrer': None}]},
            {'$set': {'user_data.referrer': owner_id,
                      'user_data.ref_tag': tag,
                      'user_data.ref_fixed_at': now()}})
        if not getattr(linked, 'modified_count', 0):
            continue

        report['linked'] += 1
        await users.col.update_one(
            {'user_data.user_id': owner_id},
            {'$addToSet': {'info.ref_stats.referrals': user_id}})

        if reward <= 0:
            continue

        await users.col.update_one(
            {'user_data.user_id': owner_id},
            {'$inc': {'info.ref_stats.withdrawable': reward,
                      'info.ref_stats.earned_total': reward,
                      'info.ref_stats.turnover_total': row['paid'],
                      'info.ref_stats.payments_count': 1},
             '$addToSet': {'info.ref_stats.paying_referrals': user_id}})

        # Начисление идёт сырым $inc мимо users.credit — журналим сами,
        # иначе в истории появятся деньги ниоткуда.
        fresh = await users.get(owner_id, {'info.ref_stats.withdrawable': 1})
        await users.record_money(
            owner_id, reward,
            f'Реферальное начисление от друга {user_id} (починка метки {tag})',
            kind='referral', auto=False, admin_id=admin_id, account='referral',
            balance_after=int(users.pick(
                fresh or {}, 'info.ref_stats.withdrawable', 0) or 0),
            meta={'friend_id': user_id, 'friend_topup': row['paid'],
                  'ref_fix': tag})

        report['reward'] += reward
        report['payers'] += 1

    log.warning('починка метки %s: связано %s, доначислено %s₽',
                tag, report['linked'], report['reward'])
    return report
