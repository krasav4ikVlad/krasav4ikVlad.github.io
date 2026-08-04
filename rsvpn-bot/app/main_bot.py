"""Точка входа бота:  python -m app.main_bot"""

from __future__ import annotations

import asyncio
import logging

from aiogram.types import BotCommand

from app.bot.factory import create_bot, create_dispatcher
from app.core.config import Config
from app.core.container import Container
from app.core.logging import setup_logging
from app.scheduler.setup import create_scheduler

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command='start', description='Перейти в меню'),
    BotCommand(command='privacy', description='Политика RS VPN'),
]


async def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    container = Container.build(config)
    await container.startup()

    bot = create_bot(container)
    dp = create_dispatcher(container)

    scheduler = create_scheduler(container, bot)
    scheduler.start()

    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info('бот запущен')
    try:
        await dp.start_polling(bot, container=container)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
