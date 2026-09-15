"""Партии в сапёра — отдельной коллекцией, ненадолго.

Почему не в памяти процесса и не в FSM: играют в это ровно во время
техработ, а техработы — это перезапуски. Поле, лежащее в MemoryStorage,
исчезнет ровно тогда, когда оно нужно, и человек вместо «продолжить»
увидит пустой экран посреди партии.

Почему не в документе пользователя: это мусор на полчаса. В карточке ему
не место, а TTL-индекс убирает его сам — без фоновой задачи и без чистки
руками.
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.repositories.base import Repository

log = logging.getLogger(__name__)

# Партия живёт сутки. Дольше незачем: техработы к этому времени кончатся, а
# «продолжить вчерашнее поле» никому не нужно.
TTL_HOURS = 24


class GamesRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('user_id', unique=True)
        await self.ensure_index('at', expireAfterSeconds=TTL_HOURS * 3600)

    async def load(self, user_id: int) -> dict | None:
        """Партия или None. Не роняет экран: играют во время техработ, а
        техработы — это ровно то время, когда база может не отвечать."""
        try:
            found = await self.col.find_one({'user_id': int(user_id)})
        except Exception as exc:
            log.warning('партия %s не прочитана: %s', user_id, exc)
            return None
        return (found or {}).get('board') or None

    async def save(self, user_id: int, board: dict, wins: int = 0) -> None:
        """Сохранить партию. Не роняет экран: игра — не то, ради чего стоит
        показывать человеку ошибку вместо поля."""
        try:
            await self.col.update_one(
                {'user_id': int(user_id)},
                {'$set': {'board': board, 'at': now()},
                 '$inc': {'wins': int(wins)},
                 '$setOnInsert': {'user_id': int(user_id)}},
                upsert=True,
            )
        except Exception as exc:
            log.warning('партия %s не сохранена: %s', user_id, exc)

    async def wins(self, user_id: int) -> int:
        try:
            found = await self.col.find_one({'user_id': int(user_id)})
        except Exception:
            return 0
        return int((found or {}).get('wins') or 0)
