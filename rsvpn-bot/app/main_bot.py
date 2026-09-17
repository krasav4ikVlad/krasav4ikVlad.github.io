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


def print_startup_banner(config) -> None:
    """Сводка режима работы. Печатается до старта, чтобы опасное сочетание
    настроек было видно сразу, а не после первого списания."""
    panel = 'ИЗМЕНЯЕТ ДАННЫЕ' if not config.vpn.dry_run else 'только чтение (dry-run)'
    scheduler = 'ВКЛЮЧЁН' if config.scheduler_enabled else 'выключен'

    from app.version import version_line

    log.info('─' * 60)
    log.info('Сборка:       %s', version_line())
    log.info('База:         %s%s', config.mongo_db,
             '  (старые имена коллекций)' if config.legacy_collections else '')
    log.info('Панель:       %s', panel)
    log.info('Планировщик:  %s', scheduler)
    log.info('Админы:       %s', ', '.join(map(str, config.admin_ids)) or '—')
    log.info('─' * 60)

    if config.scheduler_enabled and not config.vpn.dry_run:
        log.warning('Планировщик будет списывать деньги и продлевать подписки '
                    'в панели. Убедитесь, что старый бот этого больше не делает — '
                    'иначе списания пойдут дважды.')


async def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    container = Container.build(config)
    await container.startup()

    bot = create_bot(container)
    # Без этого devices, renewal, expiry, lifeline и notifier остаются None:
    # менеджер устройств падает на нажатии, админ-уведомления не уходят,
    # а планировщик тихо ничего не делает — его задачи проверяют сервис на None.
    container.attach_bot(bot)
    if container.missing_services():
        raise RuntimeError('не собраны сервисы: '
                           + ', '.join(container.missing_services()))

    dp = create_dispatcher(container)
    print_startup_banner(config)

    scheduler = None
    if config.scheduler_enabled:
        scheduler = create_scheduler(container, bot)
        scheduler.start()
    else:
        log.warning('планировщик выключен (SCHEDULER_ENABLED=0): '
                    'списаний и кампаний не будет')

    # Рассылка живёт в памяти процесса, поэтому перезапуск (в том числе
    # обычный деплой) обрывает её. Отмечаем такие при старте — дальше их
    # в течение минуты поднимет сторож в планировщике, продолжив с
    # сохранённой позиции. Руками ничего нажимать не надо.
    from app.admin.broadcast import mark_interrupted

    for job in await mark_interrupted(container):
        await container.notify(
            bot, 'campaigns',
            f'Рассылка прервана перезапуском: отправлено '
            f'{job.get("position", 0)} из {len(job.get("recipients") or [])}. '
            + ('Продолжу автоматически в течение минуты.'
               if config.scheduler_enabled else
               'Планировщик выключен — продолжить можно кнопкой под её сообщением.'))

    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info('бот запущен')
    try:
        await dp.start_polling(bot, container=container)
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
