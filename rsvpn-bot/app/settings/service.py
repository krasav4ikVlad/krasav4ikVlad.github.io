"""Сервис runtime-настроек: чтение с кэшем, запись с аудитом.

Коллекции передаются снаружи (DI), поэтому сервис тестируется на заглушке
без Mongo — см. tests/conftest.py.

Хранилище: по документу на настройку ({_id: 'price.device_extra', value: 90}).
Не один документ с вложенным объектом — в ключах есть точки, а Mongo трактует
точку в $set как путь к вложенному полю.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from app.settings.schema import INDEX, Setting, cast_value

log = logging.getLogger(__name__)

CACHE_TTL_SEC = 10.0


class SettingsService:
    def __init__(self, values_collection, audit_collection=None,
                 index: dict[str, Setting] | None = None, on_change=None):
        self._col = values_collection
        self._audit = audit_collection
        self._index = index or INDEX
        self._cache: dict[str, Any] = {}
        self._loaded_at = 0.0
        # Часть настроек живёт не только в базе, но и в памяти процесса
        # (например, тумблер кастомных эмодзи). Без этого колбэка такая
        # настройка применялась бы только после перезапуска — а тумблер,
        # который «не работает», хуже, чем его отсутствие.
        self.on_change = on_change

    @property
    def index(self) -> dict[str, Setting]:
        return self._index

    async def _load(self) -> dict[str, Any]:
        if self._cache and (time.monotonic() - self._loaded_at) < CACHE_TTL_SEC:
            return self._cache

        docs = await self._col.find({}).to_list(length=None)
        merged = {key: setting.default for key, setting in self._index.items()}
        for doc in docs:
            setting = self._index.get(doc.get('_id'))
            if setting is not None:
                merged[setting.key] = cast_value(setting, doc.get('value'))

        self._cache = merged
        self._loaded_at = time.monotonic()
        return merged

    # ── чтение ──────────────────────────────────────────────────────────────
    async def all(self) -> dict[str, Any]:
        return dict(await self._load())

    async def get(self, key: str, default: Any = None) -> Any:
        values = await self._load()
        if key in values:
            return values[key]
        setting = self._index.get(key)
        return setting.default if setting else default

    async def flag(self, key: str) -> bool:
        return bool(await self.get(key, False))

    async def int(self, key: str) -> int:
        return int(await self.get(key, 0) or 0)

    async def rate(self, key: str) -> float:
        """Процентная настройка как доля: 0.2 для 20%."""
        return float(await self.get(key, 0.0) or 0.0)

    # ── запись ──────────────────────────────────────────────────────────────
    async def set(self, key: str, value: Any, admin_id: int | None = None) -> Any:
        setting = self._index.get(key)
        if setting is None:
            raise KeyError(f'Неизвестная настройка: {key}')

        value = cast_value(setting, value)
        before = await self.get(key)

        await self._col.update_one(
            {'_id': key},
            {'$set': {'value': value, 'updated_at': datetime.now()}},
            upsert=True,
        )
        self.invalidate()

        if self._audit is not None:
            await self._audit.insert_one({
                'key': key, 'before': before, 'after': value,
                'admin_id': admin_id, 'created_at': datetime.now(),
            })

        if self.on_change is not None:
            try:
                await self.on_change(key, value)
            except Exception as exc:  # значение уже сохранено, откат не нужен
                log.warning('настройка %s сохранена, но не применена: %s', key, exc)
        return value

    async def toggle(self, key: str, admin_id: int | None = None) -> bool:
        return bool(await self.set(key, not await self.flag(key), admin_id))

    async def reset(self, key: str, admin_id: int | None = None) -> Any:
        return await self.set(key, self._index[key].default, admin_id)

    def invalidate(self) -> None:
        self._loaded_at = 0.0
