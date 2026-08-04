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


async def apply_all(container: Container) -> None:
    applied = {doc['_id'] async for doc in container.db[names.MIGRATIONS].find({})}

    import migrations
    modules = sorted(m.name for m in pkgutil.iter_modules(migrations.__path__)
                     if m.name.startswith('m'))

    for name in modules:
        if name in applied:
            continue
        module = importlib.import_module(f'migrations.{name}')
        log.info('применяю миграцию %s', name)
        await module.up(container)
        await container.db[names.MIGRATIONS].insert_one({'_id': name, 'applied_at': now()})

    log.info('миграции применены: %s', len(modules))


async def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)
    container = Container.build(config)
    await apply_all(container)


if __name__ == '__main__':
    asyncio.run(main())
