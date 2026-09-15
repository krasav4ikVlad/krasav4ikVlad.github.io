"""Локальные демо-данные: пара пользователей и тарифы. Только для разработки."""

from __future__ import annotations

import asyncio

from app.core.config import Config
from app.core.container import Container


async def main() -> None:
    config = Config.from_env()
    if config.is_production:
        raise SystemExit('Отказ: скрипт только для локальной разработки')

    container = Container.build(config)
    await container.startup()
    print('тарифы:', [p['code'] for p in await container.plans.all()])


if __name__ == '__main__':
    asyncio.run(main())
