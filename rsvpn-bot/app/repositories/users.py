"""Всё про документ пользователя.

Здесь же — атомарные операции, которые в старом коде были размазаны:
списание баланса с проверкой (сейчас можно уйти в минус при двойном клике)
и «захват слота» кампании (сейчас копия в каждом файле кампаний).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time import now
from app.repositories.base import Repository


class UsersRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('user_data.user_id', unique=True)
        await self.ensure_index('growth.segment')
        await self.ensure_index('vpn.expireAt')

    # ── чтение ──────────────────────────────────────────────────────────────
    async def get(self, user_id: int, projection: dict | None = None) -> dict | None:
        return await self.col.find_one({'user_data.user_id': user_id}, projection)

    async def exists(self, user_id: int) -> bool:
        return await self.col.count_documents({'user_data.user_id': user_id}, limit=1) > 0

    # ── создание ────────────────────────────────────────────────────────────
    async def create(self, document: dict) -> dict:
        await self.col.insert_one(document)
        return document

    # ── баланс ──────────────────────────────────────────────────────────────
    async def credit(self, user_id: int, amount: int, description: str) -> bool:
        """Начисление. Транзакция пишется единым форматом-словарём."""
        result = await self.col.update_one(
            {'user_data.user_id': user_id},
            {
                '$inc': {'info.balance': amount},
                '$push': {'info.transactions': {
                    '$each': [self._transaction(amount, description)],
                    '$slice': -500,
                }},
            },
        )
        return result.matched_count == 1

    async def charge(self, user_id: int, amount: int, description: str) -> bool:
        """Списание с проверкой в самом запросе.

        Условие 'info.balance': {'$gte': amount} внутри update гарантирует, что
        двойное нажатие кнопки не уведёт баланс в минус: второй запрос просто
        не найдёт документ. Возвращает False, если средств не хватило.
        """
        result = await self.col.update_one(
            {'user_data.user_id': user_id, 'info.balance': {'$gte': amount}},
            {
                '$inc': {'info.balance': -amount},
                '$push': {'info.transactions': {
                    '$each': [self._transaction(-amount, description)],
                    '$slice': -500,
                }},
            },
        )
        return result.modified_count == 1

    @staticmethod
    def _transaction(amount: int, description: str) -> dict:
        return {'amount': amount, 'dt': now(), 'description': description}

    # ── подписка ────────────────────────────────────────────────────────────
    async def set_vpn(self, user_id: int, vpn_fields: dict) -> None:
        await self.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {f'vpn.{key}': value for key, value in vpn_fields.items()}},
        )

    # ── кампании ────────────────────────────────────────────────────────────
    async def claim_campaign_slot(self, doc_id: Any, flag: str,
                                  extra_update: dict | None = None) -> bool:
        """Атомарно ставит флаг кампании, если его ещё нет.

        True — слот наш, сообщение отправляем; False — уже отправляли.
        Это защита от повторной отправки и повторного начисления бонуса,
        когда планировщик запускается раз в час и пересекается сам с собой.
        """
        update: dict[str, dict] = {'$set': {f'campaigns.{flag}': now()}}
        for operator, payload in (extra_update or {}).items():
            update.setdefault(operator, {})
            update[operator].update(payload)

        result = await self.col.update_one(
            {'_id': doc_id, f'campaigns.{flag}': {'$exists': False}},
            update,
        )
        return result.modified_count == 1

    # ── логи действий ───────────────────────────────────────────────────────
    async def log(self, user_id: int, action: str, details: str = '') -> None:
        await self.col.update_one(
            {'user_data.user_id': user_id},
            {'$push': {'logs': {
                '$each': [{'action': action, 'details': details, 'dt': datetime.now()}],
                '$slice': -350,
            }}},
        )
