"""Кнопка «я не качаю торренты» под уведомлением о нарушении.

Проверить это автоматически нельзя: содержимого трафика у нас нет и быть
не должно. Поэтому кнопка делает единственное разумное — зовёт человека,
который решит, и кладёт ему в карточку то, по чему решают.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types

from app.bot.callbacks import Torrent
from app.content import texts

log = logging.getLogger(__name__)


async def appeal(call: types.CallbackQuery, c) -> None:
    guard = getattr(c, 'torrents', None)
    if guard is None:
        await call.answer('Не получилось отправить, напишите в поддержку',
                          show_alert=True)
        return

    sent = await guard.appeal(call.from_user.id)
    await call.answer('Жалоба отправлена' if sent else 'Уже отправлено')
    if sent:
        # Отдельным сообщением, а не правкой уведомления: предупреждение
        # должно остаться на экране — в нём написано, что будет дальше.
        await call.message.answer(texts.render('torrent.appeal_sent'))


def create_router() -> Router:
    router = Router(name='torrents')
    router.callback_query.register(appeal, Torrent.filter(F.action == 'appeal'))
    return router
