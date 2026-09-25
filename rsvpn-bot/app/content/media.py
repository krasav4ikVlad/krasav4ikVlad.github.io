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

И номер бота — тоже часть ключа
────────────────────────────────
file_id выдаётся конкретному боту и другому не годится: Telegram отвечает
«wrong file identifier». Поэтому смена токена ломала картинки на всех
экранах разом — кэш оставался полон чужих идентификаторов, отправка падала,
и экран уходил текстом (render() гасит ошибку, чтобы не остаться совсем без
ответа). Теперь номер бота стоит в ключе: новый токен просто не находит
старых записей и заливает картинки заново.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def file_token(path: str, scope: str = '') -> str:
    """Отпечаток файла: номер бота + путь + размер + mtime."""
    try:
        stat = Path(path).stat()
        token = f'{path}:{stat.st_size}:{int(stat.st_mtime)}'
    except OSError:
        token = path
    return f'{scope}:{token}' if scope else token


def bot_scope(token: str) -> str:
    """Номер бота из токена: «123456789:AA…» → «123456789».

    Именно номер, а не сам токен: он попадает в базу как часть ключа, и
    класть туда секрет незачем.
    """
    return str(token or '').partition(':')[0]


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

    async def forget(self, token: str) -> None:
        """Забыть идентификатор: Telegram его не принял.

        Страховка на случай, когда ключа мало: file_id перестают работать и
        сами по себе (файл удалён на стороне Telegram, бот пересобран). Без
        этого один отказ означал бы экран без картинки навсегда.
        """
        self._ids.pop(token, None)
        if self._col is None:
            return
        try:
            await self._col.delete_one({'_id': token})
        except Exception as exc:
            log.warning('устаревший file_id не удалён: %s', exc)


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

    def cached(self) -> bool:
        return bool(self.cache.get(self.token))

    async def forget(self) -> None:
        await self.cache.forget(self.token)

    async def remember(self, result) -> None:
        """Достать file_id из ответа Telegram и запомнить."""
        photos = getattr(result, 'photo', None) or []
        if photos:
            await self.cache.remember(self.token, photos[-1].file_id)
