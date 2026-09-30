"""Снимок базы отдельным процессом.

    python -m scripts.dbbackup            # если пора
    python -m scripts.dbbackup --force    # в любом случае

Обычно его запускает сам бот, а не человек. Смысл отдельного процесса —
память. Снимок большой базы её ест, у процесса бота она ограничена
(`max_memory_restart` в pm2), и копия раз за разом умирала на середине:
pm2 убивал бота, тот поднимался, начинал заново — четыреста восемнадцать
перезапусков и ни одной доведённой копии.

Здесь же всё наоборот: бот остаётся лёгким и только смотрит за ходом дела
по файлу состояния, а этот процесс спокойно доводит снимок до конца.
Перезапуск бота (обновление, падение) снимок больше не обрывает: он живёт
своей жизнью и допишет файл, даже если бота в этот момент не станет.

Ход дела и результат пишутся в `attempts.json` рядом со снимками — оттуда
их и читает бот.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


async def make() -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core import db as names
    from app.core.config import Config
    from app.core.db import client_options
    from app.services.backup import BackupService
    from app.settings.service import SettingsService

    parser = argparse.ArgumentParser(description='Снимок базы')
    parser.add_argument('--force', action='store_true',
                        help='делать, даже если бот считает, что рано')
    args = parser.parse_args()

    config = Config.from_env()
    client = AsyncIOMotorClient(config.mongo_uri, **client_options())
    database = client[config.mongo_db]
    settings = SettingsService(database[names.BOT_SETTINGS])
    service = BackupService(database, settings, config.mongo_db)

    report = await service.run(force=args.force)
    if report.busy:
        print('копия уже делается — этот запуск лишний')
        return 0
    if not report.ok:
        print(f'не получилось: {report.error}', file=sys.stderr)
        return 1

    print(f'готово: {report.path}, документов {report.docs}')
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s | %(message)s')
    try:
        return asyncio.run(make())
    except KeyboardInterrupt:
        print('прервано', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
