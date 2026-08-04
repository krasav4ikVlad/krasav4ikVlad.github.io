"""Сборка бота: один вызов — и всё связано.

Здесь и только здесь известно, в каком порядке висят middleware и роутеры.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.admin import panel as admin_panel
from app.bot.handlers import register
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.errors import ErrorsMiddleware
from app.bot.middlewares.maintenance import MaintenanceMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.middlewares.user import UserMiddleware
from app.core.container import Container


def create_bot(container: Container) -> Bot:
    return Bot(
        token=container.config.bot_token,
        default=DefaultBotProperties(parse_mode='HTML'),
    )


def create_dispatcher(container: Container, storage=None) -> Dispatcher:
    dp = Dispatcher(storage=storage or MemoryStorage())

    # порядок важен: сначала ошибки (внешний слой), потом зависимости,
    # затем троттлинг и техработы, и только потом загрузка пользователя
    middlewares = (
        ErrorsMiddleware(),
        DependenciesMiddleware(container),
        ThrottleMiddleware(),
        MaintenanceMiddleware(container.settings, container.config.admin_ids),
        UserMiddleware(container.users),
    )
    for middleware in middlewares:
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)

    dp.include_router(admin_panel.create_router(container.config.admin_ids))
    register(dp)
    return dp
