"""Картинки экранов и кэш их file_id.

Каждый экран бота — фотография с подписью. Отправляя `FSInputFile`, бот
заново загружает файл в Telegram: на каждое нажатие кнопки уезжает весь PNG.
Именно отсюда бралась секундная пауза перед обновлением сообщения.

Telegram на первую загрузку отвечает `file_id`, и дальше ту же картинку можно
отправлять одной строкой — без тела файла. Здесь этот идентификатор
запоминается и сохраняется в Mongo, чтобы перезапуск бота не начинал всё
заново.

Ключ кэша включает размер и время изменения файла: заменили картинку в
media/ — ключ поменялся, и она перезальётся сама. Иначе бот показывал бы
старое изображение до ручной чистки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def file_token(path: str) -> str:
    """Отпечаток файла: путь + размер + mtime."""
    try:
        stat = Path(path).stat()
        return f'{path}:{stat.st_size}:{int(stat.st_mtime)}'
    except OSError:
        return path


class MediaCache:
    """file_id загруженных картинок. Работает и без коллекции — просто в памяти."""

    def __init__(self, collection=None):
        self._col = collection
        self._ids: dict[str, str] = {}

    async def load(self) -> None:
        """Прогрев при старте: первые показы экранов идут уже без загрузки."""
        if self._col is None:
            return
        try:
            docs = await self._col.find({}).to_list(length=None)
        except Exception as exc:
            log.warning('кэш картинок не прочитан: %s', exc)
            return
        self._ids = {d['_id']: d['file_id'] for d in docs
                     if d.get('_id') and d.get('file_id')}
        log.info('кэш картинок: %s шт.', len(self._ids))

    def get(self, token: str) -> str | None:
        return self._ids.get(token)

    async def remember(self, token: str, file_id: str) -> None:
        if not file_id or self._ids.get(token) == file_id:
            return
        self._ids[token] = file_id
        if self._col is None:
            return
        try:
            await self._col.update_one({'_id': token},
                                       {'$set': {'file_id': file_id}}, upsert=True)
        except Exception as exc:      # кэш — ускорение, а не обязательство
            log.warning('file_id не сохранён: %s', exc)


@dataclass(frozen=True)
class Photo:
    """Картинка экрана: чем отправлять и куда запомнить ответ Telegram."""
    path: str
    token: str
    cache: MediaCache

    def as_input(self):
        """file_id, если картинка уже загружена, иначе сам файл."""
        from aiogram.types import FSInputFile

        return self.cache.get(self.token) or FSInputFile(self.path)

    async def remember(self, result) -> None:
        """Достать file_id из ответа Telegram и запомнить."""
        photos = getattr(result, 'photo', None) or []
        if photos:
            await self.cache.remember(self.token, photos[-1].file_id)
