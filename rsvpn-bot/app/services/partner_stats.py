"""Сколько людей и денег приносит партнёрская ссылка — по дням.

Вопрос, который задают про партнёра, всегда один и тот же: «как у него
идёт». Сумма за всё время на него не отвечает — по ней не видно ни
всплеска после ролика, ни того, что ссылка неделю назад перестала
работать. Отвечают дни подряд.

Считается по метке в карточке (`user_data.ref_tag`), а не по
`referrer`: метка ставится при регистрации и означает ровно «пришёл по
этой ссылке». Пришедшие по числовой ссылке того же человека сюда не
попадают — это другая ссылка и другой разговор.

Деньги считаются по всем, кто когда-либо пришёл по метке, а не только по
пришедшим за период: человек, зарегистрировавшийся в августе, платит
сегодня, и это сегодняшние деньги партнёра.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.core.time import now, parse_dt

log = logging.getLogger(__name__)

CHUNK = 500
BAR = '█'
BAR_WIDTH = 10


def bar(value: int, top: int) -> str:
    """Полоска на строку дня: столбик виден одним взглядом, число — нет."""
    if top <= 0 or value <= 0:
        return ''
    return BAR * max(1, round(value * BAR_WIDTH / top))


async def by_day(users, journal, *, tag: str, days: int = 14) -> dict:
    """Регистрации и оплаты по дням за последние `days` дней."""
    border = (now() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    ids: list[int] = []
    joined: dict[str, int] = {}
    total_people = 0

    cursor = users.col.find({'user_data.ref_tag': tag},
                            {'user_data.user_id': 1,
                             'user_data.date_joined': 1})
    async for doc in cursor:
        user_id = users.pick(doc, 'user_data.user_id')
        if user_id is None:
            continue
        ids.append(int(user_id))
        total_people += 1

        at = parse_dt(users.pick(doc, 'user_data.date_joined'))
        if at and at >= border:
            key = at.strftime('%d.%m')
            joined[key] = joined.get(key, 0) + 1

    paid = await _paid_by_day(journal, ids, border)

    rows = []
    for shift in range(days):
        moment = border + timedelta(days=shift)
        key = moment.strftime('%d.%m')
        rows.append({'day': key, 'people': joined.get(key, 0),
                     'paid': paid.get(key, 0)})

    return {
        'tag': tag,
        'days': rows,
        'total_people': total_people,
        'people': sum(row['people'] for row in rows),
        'paid': sum(row['paid'] for row in rows),
        'today': rows[-1] if rows else {'people': 0, 'paid': 0},
    }


async def _paid_by_day(journal, ids: list[int], border) -> dict[str, int]:
    """Оплаты его людей по дням. Считаем внесённое, а не зачисленное:
    бонус за пополнение — наш подарок, и к его работе отношения не имеет."""
    totals: dict[str, int] = {}
    unique = list(dict.fromkeys(ids))

    for begin in range(0, len(unique), CHUNK):
        chunk = unique[begin:begin + CHUNK]
        cursor = journal.col.find(
            {'kind': 'topup', 'user_id': {'$in': chunk},
             'at': {'$gte': border}},
            {'amount': 1, 'meta': 1, 'at': 1})
        async for row in cursor:
            at = parse_dt(row.get('at'))
            if at is None or at < border:
                continue
            meta = row.get('meta') or {}
            amount = int(meta.get('paid') or row.get('amount') or 0)
            if amount <= 0:
                continue
            key = at.strftime('%d.%m')
            totals[key] = totals.get(key, 0) + amount
    return totals
