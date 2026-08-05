"""Единый формат транзакций.

Исторически в info.transactions лежат и списки [amount, dt, description],
и словари. Миграция приводит всё к словарю.

НЕОБЯЗАТЕЛЬНАЯ. Оба бота читают оба формата (app/domain/transactions.py),
поэтому без неё всё работает.

И её НЕЛЬЗЯ запускать, пока принимаются платежи: массив транзакций
перезаписывается целиком, и пополнение, пришедшее между чтением и записью,
из массива пропадёт (баланс при этом не пострадает — он меняется отдельным
$inc). Запускать в окно, когда бот и приём вебхуков остановлены, и передать
--force, чтобы подтвердить, что окно есть.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


async def up(container) -> None:
    if os.getenv('MIGRATE_FORCE', '').lower() not in ('1', 'true', 'yes'):
        log.warning('m0002 пропущена: перезаписывает транзакции целиком. '
                    'Запускать при остановленных боте и вебхуках, '
                    'с MIGRATE_FORCE=1')
        return

    await _convert(container)


async def _convert(container) -> None:
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
