"""Блокировка пользователей.

Флаг лежит в самом документе пользователя (`moderation.banned`), а не в
отдельной коллекции: он нужен на КАЖДОМ обновлении, а документ и так
читается middleware — лишнего запроса не появляется.

Что здесь намеренно не делается: подписка и деньги не трогаются. Бан — это
запрет на общение с ботом, а не изъятие оплаченного. Разбанили — человек
продолжает с того же места.
"""

from __future__ import annotations

import logging

from app.core.time import now

log = logging.getLogger(__name__)


class ModerationService:
    def __init__(self, users, settings):
        self.users = users
        self.settings = settings

    @staticmethod
    def is_banned(user: dict | None) -> bool:
        return bool(((user or {}).get('moderation') or {}).get('banned'))

    async def ban(self, user_id: int, admin_id: int, reason: str = '') -> bool:
        """Забанить. False — такого пользователя нет."""
        result = await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'moderation.banned': True, 'moderation.banned_at': now(),
                      'moderation.banned_by': admin_id, 'moderation.reason': reason}},
        )
        if result.matched_count:
            log.info('забанен %s админом %s: %s', user_id, admin_id, reason or 'без причины')
        return bool(result.matched_count)

    async def unban(self, user_id: int, admin_id: int) -> bool:
        result = await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'moderation.banned': False, 'moderation.unbanned_at': now(),
                      'moderation.unbanned_by': admin_id}},
        )
        if result.matched_count:
            log.info('разбанен %s админом %s', user_id, admin_id)
        return bool(result.matched_count)

    async def banned(self, limit: int = 50) -> list[dict]:
        return await self.users.col.find(
            {'moderation.banned': True},
            {'user_data.user_id': 1, 'user_data.username': 1, 'moderation': 1},
        ).limit(limit).to_list(length=limit)

    async def count(self) -> int:
        return await self.users.col.count_documents({'moderation.banned': True})

    async def find_user(self, query: str) -> dict | None:
        """Пользователь по id или юзернейму — чтобы банить не только по цифрам."""
        query = query.strip().lstrip('@')
        if query.isdigit():
            return await self.users.get(int(query))
        return await self.users.col.find_one({'user_data.username': query})
