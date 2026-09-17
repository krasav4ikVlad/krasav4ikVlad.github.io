"""Запас поднятых машин: сквады, готовые к выдаче.

Провижининг ручной, и это узкое место: заявка ждёт, пока человек поднимет
VPS. Поднимать их заранее — единственный способ выдавать сразу, но тогда
нужно где-то помнить, какие машины уже стоят и чем заняты.

Здесь только список. Занята машина или свободна, считается по самим
серверам (app/services/private_servers.py): держать это отдельным полем
значит завести второй источник правды, который однажды разойдётся с первым.
"""

from __future__ import annotations

from app.core.time import now
from app.repositories.base import Repository


class ServerPoolRepository(Repository):
    async def ensure_indexes(self) -> None:
        # один сквад — одна запись: добавить его дважды означает выдать
        # одну машину двум людям
        await self.ensure_index('squad_uuid', unique=True)

    async def add(self, squad_uuid: str, location: str, profile: str,
                  added_by: int = 0, note: str = '') -> bool:
        """Записать машину в запас. False — такая уже есть."""
        existing = await self.col.find_one({'squad_uuid': squad_uuid})
        if existing:
            return False

        await self.col.insert_one({
            'squad_uuid': squad_uuid,
            'location': location,
            'profile': profile,
            'note': note,
            'added_by': added_by,
            'added_at': now(),
        })
        return True

    async def remove(self, squad_uuid: str) -> bool:
        result = await self.col.delete_one({'squad_uuid': squad_uuid})
        return getattr(result, 'deleted_count', 0) == 1

    async def get(self, squad_uuid: str) -> dict | None:
        return await self.col.find_one({'squad_uuid': squad_uuid})

    async def all(self) -> list[dict]:
        return await self.col.find({}).sort('added_at', 1).to_list(length=500)
