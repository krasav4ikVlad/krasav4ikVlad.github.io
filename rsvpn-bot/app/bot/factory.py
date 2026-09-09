"""Сборка бота: один вызов — и всё связано.

Здесь и только здесь известно, в каком порядке висят middleware и роутеры.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.admin import panel as admin_panel
from app.bot.handlers import register
from app.bot.middlewares.actions import ActionLogMiddleware
from app.bot.middlewares.ban import BanMiddleware
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.emoji import emoji_middleware
from app.bot.middlewares.errors import ErrorsMiddleware
from app.bot.middlewares.maintenance import MaintenanceMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.middlewares.user import UserMiddleware
from app.core.container import Container


def create_bot(container: Container) -> Bot:
    bot = Bot(
        token=container.config.bot_token,
        default=DefaultBotProperties(parse_mode='HTML'),
    )
    # кастомные эмодзи навешиваются здесь, на выходе: см. middlewares/emoji.py
    bot.session.middleware(emoji_middleware)
    return bot


def create_dispatcher(container: Container, storage=None) -> Dispatcher:
    dp = Dispatcher(storage=storage or MemoryStorage())

    # Внешние middleware отрабатывают ДО фильтров — иначе фильтр Feature()
    # не увидит settings и пропустит выключенный раздел внутрь хендлера.
    outer = (
        # первым: строка в лог должна появиться и у того действия, которое
        # дальше отвалится по фильтру или упадёт с ошибкой
        ActionLogMiddleware(container),
        ErrorsMiddleware(container),
        DependenciesMiddleware(container),
        ThrottleMiddleware(),
        MaintenanceMiddleware(container.settings, container.config.admin_ids),
        UserMiddleware(container.users),
        # строго после UserMiddleware: документ уже прочитан, отдельного
        # запроса за флагом бана не появляется
        BanMiddleware(container.settings, container.config.admin_ids),
    )
    for middleware in outer:
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)
    # Инлайн-режим — тоже обновление, и ему нужны и зависимости, и перехват
    # ошибок: без ErrorsMiddleware падение хендлера выглядит для человека как
    # «бот не отвечает на упоминание в чате», а в логах не остаётся ничего.
    dp.inline_query.outer_middleware(ActionLogMiddleware(container))
    dp.inline_query.outer_middleware(ErrorsMiddleware(container))
    dp.inline_query.outer_middleware(DependenciesMiddleware(container))
    # Во время техработ подарки через инлайн тоже не выдаются: иначе «ничего
    # нельзя» обходится одним упоминанием бота в чужом чате.
    dp.inline_query.outer_middleware(
        MaintenanceMiddleware(container.settings, container.config.admin_ids))

    dp.include_router(admin_panel.create_router(container.config.admin_ids))
    register(dp)
    return dp
