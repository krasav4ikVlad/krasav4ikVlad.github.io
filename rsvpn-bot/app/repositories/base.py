"""База для репозиториев.

Репозиторий — единственное место, которое знает структуру документов в Mongo.
Ни хендлеры, ни сервисы не пишут users.update_one({'user_data.user_id': ...})
напрямую: иначе схема расползается по 3000 строк, как сейчас.
"""

from __future__ import annotations

from typing import Any, AsyncIterator


class Repository:
    def __init__(self, collection):
        self.col = collection

    async def ensure_indexes(self) -> None:
        """Переопределяется в наследниках. Вызывается миграцией на старте."""

    async def iterate(self, query: dict, projection: dict | None = None) -> AsyncIterator[dict]:
        cursor = self.col.find(query, projection)
        async for doc in cursor:
            yield doc

    async def count(self, query: dict | None = None) -> int:
        return await self.col.count_documents(query or {})

    @staticmethod
    def pick(doc: dict, path: str, default: Any = None) -> Any:
        """Безопасный доступ по пути: pick(user, 'info.ref_stats.balance', 0)."""
        current: Any = doc
        for part in path.split('.'):
            if not isinstance(current, dict):
                return default
            current = current.get(part)
        return default if current is None else current
