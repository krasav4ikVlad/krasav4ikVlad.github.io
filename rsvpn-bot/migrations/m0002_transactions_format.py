"""Единый формат транзакций.

Исторически в info.transactions лежат и списки [amount, dt, description],
и словари. Из-за этого любой подсчёт пополнений разбирает оба варианта.
Миграция приводит всё к словарю.
"""

from __future__ import annotations


async def up(container) -> None:
    users = container.users.col
    async for user in users.find({'info.transactions.0': {'$exists': True}}):
        transactions = user.get('info', {}).get('transactions') or []
        converted = []
        changed = False

        for item in transactions:
            if isinstance(item, list) and len(item) >= 3:
                converted.append({'amount': item[0], 'dt': item[1], 'description': str(item[2])})
                changed = True
            elif isinstance(item, dict):
                converted.append(item)

        if changed:
            await users.update_one({'_id': user['_id']},
                                   {'$set': {'info.transactions': converted}})
