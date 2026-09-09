"""Разбор транзакций. Понимает оба формата, которые есть в базе.

Исторически в info.transactions лежат и списки `[amount, dt, description]`,
и словари `{amount, dt, description}`. Старый бот тоже разбирает оба — значит
переводить базу в единый формат не обязательно, и новый бот работает на данных
как есть.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time import parse_dt

TOPUP_MARKERS = ('пополнение', 'topup')


def extract(tx: Any) -> tuple[float | None, datetime | None, str | None]:
    """(сумма, дата, описание) из транзакции любого формата."""
    if isinstance(tx, dict):
        return (tx.get('amount'), parse_dt(tx.get('dt') or tx.get('created_at')),
                tx.get('description') or tx.get('type') or tx.get('comment'))

    # встречается и обёртка [ {...} ]
    if isinstance(tx, list) and len(tx) == 1 and isinstance(tx[0], dict):
        return extract(tx[0])

    if isinstance(tx, list) and len(tx) >= 3:
        amount = tx[0] if isinstance(tx[0], (int, float)) else None
        return amount, parse_dt(tx[1]), tx[2] if isinstance(tx[2], str) else None

    return None, None, None


def is_topup(tx: Any) -> bool:
    amount, _, description = extract(tx)
    if amount is None or float(amount) <= 0 or not description:
        return False
    lowered = str(description).lower()
    return any(marker in lowered for marker in TOPUP_MARKERS)


def topup_stats(transactions: list) -> dict:
    """Сводка по пополнениям: сколько раз, на сколько, первое и последнее."""
    count = 0
    total = 0.0
    first: datetime | None = None
    last: datetime | None = None

    for tx in transactions or []:
        if not is_topup(tx):
            continue
        amount, dt, _ = extract(tx)
        count += 1
        total += float(amount)
        if dt:
            first = dt if first is None or dt < first else first
            last = dt if last is None or dt > last else last

    return {'has_topup': count > 0, 'topups_count': count,
            'topups_total': total, 'first_topup_at': first, 'last_topup_at': last}
