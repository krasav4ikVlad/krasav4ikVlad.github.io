"""Платежи и вебхуки.

Идемпотентность живёт здесь: ровно один способ сказать «этот txid уже
обработан», вместо копии проверки в каждом обработчике вебхука.
"""

from __future__ import annotations

from app.core.time import now
from app.repositories.base import Repository


class PaymentsRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('txid', unique=True)
        await self.ensure_index('user_id')

    async def register(self, txid: str, user_id: int, amount: int,
                       provider: str, payload: dict) -> bool:
        """True — платёж новый и его нужно провести. False — дубль вебхука."""
        result = await self.col.update_one(
            {'txid': txid},
            {'$setOnInsert': {
                'txid': txid, 'user_id': user_id, 'amount': amount,
                'provider': provider, 'payload': payload,
                'created_at': now(), 'status': 'new',
            }},
            upsert=True,
        )
        return result.upserted_id is not None

    async def mark(self, txid: str, status: str, **fields) -> None:
        await self.col.update_one(
            {'txid': txid},
            {'$set': {'status': status, 'updated_at': now(), **fields}},
        )
