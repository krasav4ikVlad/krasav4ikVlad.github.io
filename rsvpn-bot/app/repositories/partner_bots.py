"""Партнёрские боты — витрины, которые ведут в основного бота.

Партнёр рекламирует своего бота, а не ссылку на нашего. Разница в том, что
ссылку теряют: её вырезают при пересылке, её не набирают руками, по ней не
приходят те, кто просто нашёл бота поиском. А имя бота — это и есть то, что
запоминают и пересылают, и любой путь к нему несёт метку партнёра.

Внутри такой бот умеет ровно одно: поздороваться и дать кнопку в основного
бота со ссылкой `?start=ref_<метка>`. Поэтому отдельный процесс ему не
нужен — он живёт вебхуком в том же API, что принимает платежи.

Токен здесь хранится потому, что без него бот не ответит. Это ключ к чужому
боту: он не показывается целиком ни на одном экране и не попадает в URL
вебхука — для адреса заводится отдельный случайный секрет.
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.repositories.base import Repository

log = logging.getLogger(__name__)


class PartnerBotsRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('secret', unique=True)
        await self.ensure_index('token', unique=True)
        await self.ensure_index('tag')

    async def add(self, token: str, secret: str, tag: str, user_id: int,
                  username: str = '', title: str = '') -> bool:
        """Записать бота. False — такой токен уже заведён."""
        try:
            await self.col.insert_one({
                'token': token,
                # Секрет — адрес вебхука и заодно то, что Telegram присылает
                # в заголовке. Токен в адресе оседал бы в логах прокси.
                'secret': secret,
                'tag': tag,
                'user_id': int(user_id),
                'username': username or '',
                'title': title or '',
                'created_at': now(),
                'starts': 0,
                'last_at': None,
            })
            return True
        except Exception as exc:
            log.info('партнёрский бот не заведён: %s', exc)
            return False

    async def by_secret(self, secret: str) -> dict | None:
        return await self.col.find_one({'secret': secret})

    async def by_username(self, username: str) -> dict | None:
        return await self.col.find_one({'username': (username or '').lstrip('@')})

    async def count_start(self, secret: str) -> None:
        """Отметить нажатие /start. Не роняет ответ человеку."""
        try:
            await self.col.update_one(
                {'secret': secret},
                {'$inc': {'starts': 1}, '$set': {'last_at': now()}})
        except Exception as exc:
            log.warning('старт партнёрского бота не посчитан: %s', exc)

    async def remove(self, username_or_tag: str) -> dict | None:
        """Убрать бота. Возвращает удалённую запись — по ней снимаем вебхук."""
        query = {'$or': [{'username': (username_or_tag or '').lstrip('@')},
                         {'tag': username_or_tag}]}
        found = await self.col.find_one(query)
        if not found:
            return None
        await self.col.delete_one({'_id': found['_id']})
        return found

    async def all(self, limit: int = 100) -> list[dict]:
        return await self.col.find({}).sort('starts', -1).to_list(length=limit)
