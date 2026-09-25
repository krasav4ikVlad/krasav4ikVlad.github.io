"""Миграции Mongo: применяются один раз, версия хранится в БД.

Нужны, потому что схема документов всё равно меняется (например, привести
info.transactions к единому формату-словарю), а править прод руками из
консоли — способ потерять данные.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import pkgutil

from app.core import db as names
from app.core.config import Config
from app.core.container import Container
from app.core.logging import setup_logging
from app.core.time import now

log = logging.getLogger(__name__)


async def apply_all(container: Container, only: list[str] | None = None,
                    skip: list[str] | None = None,
                    redo: list[str] | None = None) -> None:
    applied = {doc['_id'] async for doc in container.db[names.MIGRATIONS].find({})}

    for prefix in redo or []:
        for name in list(applied):
            if name.startswith(prefix):
                await container.db[names.MIGRATIONS].delete_one({'_id': name})
                applied.discard(name)
                log.info('%s — отметка снята, будет выполнена заново', name)

    import migrations
    modules = sorted(m.name for m in pkgutil.iter_modules(migrations.__path__)
                     if m.name.startswith('m'))

    done = 0
    for name in modules:
        if name in applied:
            log.info('%s — уже применена', name)
            continue
        if only and not any(name.startswith(prefix) for prefix in only):
            log.info('%s — пропущена (--only)', name)
            continue
        if skip and any(name.startswith(prefix) for prefix in skip):
            log.info('%s — пропущена (--skip)', name)
            continue

        module = importlib.import_module(f'migrations.{name}')
        log.info('применяю %s', name)
        await module.up(container)
        await container.db[names.MIGRATIONS].insert_one({'_id': name, 'applied_at': now()})
        done += 1

    log.info('выполнено миграций: %s из %s', done, len(modules))


async def main(only=None, skip=None, redo=None) -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    log.info('База: %s%s', config.mongo_db,
             '  (старые имена коллекций)' if config.legacy_collections else '')

    container = Container.build(config)
    await apply_all(container, only=only, skip=skip, redo=redo)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Миграции базы')
    parser.add_argument('--only', nargs='*', help='выполнить только эти, например m0001')
    parser.add_argument('--skip', nargs='*', help='пропустить эти')
    parser.add_argument('--redo', nargs='*',
                        help='выполнить заново, даже если отмечена как применённая')
    args = parser.parse_args()

    asyncio.run(main(args.only, args.skip, args.redo))
