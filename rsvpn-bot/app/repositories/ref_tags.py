"""Именные ссылки приглашения — отдельной коллекцией.

Раньше они жили одной строкой в настройках: `blog:123456,vk:654321`.
Для двух-трёх меток этого хватало, но добавить четвёртую значило
перепечатать всю строку, а посчитать, сколько привела каждая, было нельзя
вообще — а ровно за этим именные ссылки и нужны.

Поэтому коллекция: метка уникальна индексом, у каждой свой счётчик
регистраций, и видно, когда её выдали и кому.
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.domain import ref_tags as domain
from app.repositories.base import Repository

log = logging.getLogger(__name__)


class RefTagsRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('tag', unique=True)
        await self.ensure_index('user_id')

    async def create(self, tag: str, user_id: int, note: str = '',
                     admin_id: int = 0) -> bool:
        """Занять метку. False — такая уже есть.

        Уникальность держит индекс, а не проверка перед вставкой: между
        проверкой и вставкой успевает пройти второй вызов, и тогда одна
        метка вела бы к двум людям — то есть деньги уходили бы не тому.
        """
        try:
            await self.col.insert_one({
                'tag': domain.normalize(tag),
                'user_id': int(user_id),
                'note': note or '',
                'created_at': now(),
                'created_by': int(admin_id or 0),
                'registrations': 0,
                'last_at': None,
            })
            return True
        except Exception as exc:
            log.info('метка %s не занята: %s', tag, exc)
            return False

    async def get(self, tag: str) -> dict | None:
        return await self.col.find_one({'tag': domain.normalize(tag)})

    async def update(self, tag: str, **fields) -> bool:
        """Поменять настройки метки: чат уведомлений, триал без подписки."""
        result = await self.col.update_one(
            {'tag': domain.normalize(tag)}, {'$set': fields})
        return bool(getattr(result, 'matched_count', 0))

    async def of_partner(self, tag: str) -> dict | None:
        """Метка вместе с её настройками — по ней живёт весь партнёр."""
        return await self.get(tag)

    async def owner(self, tag: str) -> int:
        """Кому принадлежит метка. 0 — метки нет."""
        found = await self.col.find_one({'tag': domain.normalize(tag)})
        return int((found or {}).get('user_id') or 0)

    async def count_hit(self, tag: str) -> None:
        """Отметить регистрацию по метке. Не роняет регистрацию человека."""
        try:
            await self.col.update_one(
                {'tag': domain.normalize(tag)},
                {'$inc': {'registrations': 1}, '$set': {'last_at': now()}})
        except Exception as exc:
            log.warning('переход по метке %s не посчитан: %s', tag, exc)

    async def remove(self, tag: str) -> bool:
        result = await self.col.delete_one({'tag': domain.normalize(tag)})
        return bool(getattr(result, 'deleted_count', 0))

    async def all(self, limit: int = 200) -> list[dict]:
        return await self.col.find({}).sort('registrations', -1).to_list(length=limit)

    async def of_user(self, user_id: int, limit: int = 20) -> list[dict]:
        """Метки одного человека. Их может быть несколько — по одной на канал."""
        return await self.col.find({'user_id': int(user_id)}).sort(
            'registrations', -1).to_list(length=limit)
