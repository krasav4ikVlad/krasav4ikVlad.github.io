"""Режим техработ из командной строки.

    python -m scripts.maintenance on
    python -m scripts.maintenance off
    python -m scripts.maintenance          # только показать

Тот же тумблер, что кнопка в /admin, но доступный до запуска бота и после
его остановки. Именно это и нужно при обновлении: включить режим надо
раньше, чем бот будет остановлен, а выключить — когда он уже поднялся, и
лезть за телефоном в середине выкладки неудобно.

Пишет прямо в настройки в Mongo. Бот перечитывает их сам, перезапуск ради
одного флага не нужен.
"""

from __future__ import annotations

import asyncio
import sys

KEY = 'features.maintenance_mode'


async def main() -> int:
    from app.core import db as names
    from app.core.config import Config
    from app.settings.service import SettingsService

    action = (sys.argv[1] if len(sys.argv) > 1 else '').strip().lower()
    if action not in ('', 'on', 'off', 'вкл', 'выкл'):
        print(f'Не понял «{action}». Нужно: on, off или ничего.')
        return 2

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        print(f'Конфиг: {exc}')
        return 1

    # Драйвер импортируем после проверки конфига: незаполненный .env — самая
    # частая причина неудачи, и отвечать на неё стоит своей строкой, а не
    # трассировкой из чужой библиотеки.
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(config.mongo_uri, tz_aware=True,
                                serverSelectionTimeoutMS=8000)
    settings = SettingsService(client[config.mongo_db][names.BOT_SETTINGS])

    try:
        if action in ('on', 'вкл'):
            await settings.set(KEY, True)
        elif action in ('off', 'выкл'):
            await settings.set(KEY, False)

        # Читаем всегда — и после записи тоже: печатать «включено» по своему
        # же намерению, не спросив базу, значит однажды соврать.
        state = await settings.flag(KEY)
    except Exception as exc:
        print(f'База не ответила: {exc}')
        return 1
    finally:
        client.close()

    print('Техработы ВКЛЮЧЕНЫ — бот отвечает людям только экраном ожидания.'
          if state else
          'Техработы выключены — бот работает обычно.')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
