"""Последний роутер: всё, что не разобрали разделы выше.

Важно: этот роутер подключается последним, иначе он перехватит сообщения,
адресованные FSM-состояниям других разделов (в старом боте catch-all в
start.py именно поэтому требовал подключать survey_router раньше него).
"""

from __future__ import annotations

from aiogram import F, Router, types

from app.bot.handlers.profile import show_profile

async def anything_else(message: types.Message, c, user: dict | None, settings):
    if user is None:
        await message.answer('Отправьте /start, чтобы начать.')
        return
    await show_profile(message, c, user, settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='fallback')
    router.message.register(anything_else, F.chat.type == 'private')
    return router
