"""База для репозиториев.

Репозиторий — единственное место, которое знает структуру документов в Mongo.
Ни хендлеры, ни сервисы не пишут users.update_one({'user_data.user_id': ...})
напрямую: иначе схема расползается по 3000 строк, как сейчас.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

# Коды ошибок Mongo, которые обрабатываются осмысленно
INDEX_CONFLICT = 86      # IndexKeySpecsConflict: индекс с тем же именем, другие параметры
DUPLICATE_KEY = 11000    # в данных есть дубли — уникальный индекс не построить


def index_name(keys) -> str:
    """Имя, которое Mongo генерирует сама: поле_1, поле_-1, a_1_b_1."""
    if isinstance(keys, str):
        return f'{keys}_1'
    return '_'.join(f'{field}_{direction}' for field, direction in keys)


class Repository:
    def __init__(self, collection):
        self.col = collection

    async def ensure_index(self, keys, unique: bool = False,
                           only_existing: bool = False, **kwargs) -> bool:
        """Создать индекс, разобравшись с тем, что уже есть в базе.

        Три случая, из-за которых обычный create_index падает на живой базе:

        * индекс с таким именем уже есть, но с другими параметрами — например
          неуникальный `user_data.user_id_1`, созданный руками для ускорения
          поиска дублей. Mongo не меняет параметры на лету, поэтому старый
          индекс удаляется и создаётся заново;
        * в данных остались дубли — тогда уникальный индекс не построить,
          и об этом нужно сказать понятно, а не падать трейсбеком;
        * в коллекции есть документы, где поля вообще нет. Для уникального
          индекса все они одинаковы (null), поэтому второй такой документ
          считается дублем — хотя по значению дублей нет. Лечится
          only_existing=True: уникальность проверяется только там, где поле
          есть, а мусорные документы просто не участвуют.
        """
        if only_existing and isinstance(keys, str):
            kwargs.setdefault('partialFilterExpression', {keys: {'$exists': True}})

        try:
            await self.col.create_index(keys, unique=unique, **kwargs)
            return True
        except Exception as exc:
            code = getattr(exc, 'code', None)

            if code == INDEX_CONFLICT:
                name = index_name(keys)
                log.info('индекс %s пересоздаётся с unique=%s', name, unique)
                try:
                    await self.col.drop_index(name)
                    await self.col.create_index(keys, unique=unique, **kwargs)
                    return True
                except Exception as retry:
                    code = getattr(retry, 'code', None)
                    exc = retry

            if code == DUPLICATE_KEY:
                log.error('индекс %s не создан: в коллекции %s остались дубли. '
                          'Выполните: python -m scripts.dedupe --collection %s',
                          index_name(keys), self.col.name, self.col.name)
                return False

            log.warning('индекс %s не создан: %s', index_name(keys), exc)
            return False

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
