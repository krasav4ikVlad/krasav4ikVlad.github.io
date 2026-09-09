"""Ошибки, которые увидел пользователь.

«Сервис подписок не отвечает» — честный текст для человека и бесполезный
для того, кто чинит: кто именно, когда, на каком экране и что на самом деле
ответила панель, в нём нет. Всё это уходило в лог на сервере строкой уровня
INFO, без имени пользователя, и найти там конкретный случай можно было
только зная время с точностью до минуты.

Поэтому каждая показанная ошибка ложится сюда: с человеком, экраном и
настоящим текстом ошибки. Смотреть — командой /errors.

Коллекция чистится сама: записи старше месяца удаляются по TTL-индексу.
Держать их дольше незачем, а расти бесконечно она не должна.
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.repositories.base import Repository

log = logging.getLogger(__name__)

# Сколько хранить. Месяца хватает: вопросы «а что было вчера» приходят в
# течение недели, всё старше — материал для отчёта, а не для разбора.
TTL_DAYS = 30


class ErrorLogRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index([('at', -1)])
        await self.ensure_index([('user_id', 1), ('at', -1)])
        # Mongo удалит записи сам — в отличие от массивов в документах, здесь
        # это делается одной строкой и без фонового скрипта.
        await self.ensure_index('at', expireAfterSeconds=TTL_DAYS * 86400)

    async def record(self, user_id: int, kind: str, error: str, message: str,
                     where: str = '', username: str = '', shown: str = '') -> None:
        """Записать. Никогда не роняет обработчик: он и так уже упал."""
        try:
            await self.col.insert_one({
                'at': now(),
                'user_id': int(user_id or 0),
                'username': username or '',
                # panel — панель отказала, app — ожидаемая ошибка бота,
                # crash — то, чего мы не предусмотрели
                'kind': kind,
                'error': error,          # класс исключения
                'message': message,      # настоящий текст, с кодом и ответом
                'where': where,          # экран или команда, где случилось
                'shown': shown,          # что при этом увидел человек
            })
        except Exception as exc:
            log.warning('ошибка %s не записана в журнал: %s', error, exc)

    async def recent(self, limit: int = 20, user_id: int = 0,
                     kind: str = '') -> list[dict]:
        query: dict = {}
        if user_id:
            query['user_id'] = int(user_id)
        if kind:
            query['kind'] = kind
        return await self.col.find(query).sort('at', -1).to_list(length=limit)

    async def count_since(self, moment, kind: str = '') -> int:
        query: dict = {'at': {'$gte': moment}}
        if kind:
            query['kind'] = kind
        return await self.col.count_documents(query)
