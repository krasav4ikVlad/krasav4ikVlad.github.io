"""Копия базы для тестового контура.

    python -m scripts.clone_db --from RS_2 --to RS_2_test

Читает исходную базу и складывает документы в целевую. Исходную не трогает
вообще: только find(). Целевая перед копированием очищается — поэтому скрипт
отказывается работать, если имена совпадают.

Зачем: тестировать нового бота на реальных данных, не рискуя боевыми.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

BATCH = 500


async def clone(uri: str, source_name: str, target_name: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    if source_name == target_name:
        print('❌ Исходная и целевая база совпадают — так копировать нельзя.')
        return 1

    client = AsyncIOMotorClient(uri, tz_aware=True)
    source = client[source_name]
    target = client[target_name]

    names = [n for n in await source.list_collection_names()]
    print(f'Копирую {len(names)} коллекций: {source_name} → {target_name}\n')

    for name in names:
        await target[name].drop()
        total = 0
        batch: list[dict] = []

        async for doc in source[name].find({}):
            batch.append(doc)
            if len(batch) >= BATCH:
                await target[name].insert_many(batch)
                total += len(batch)
                batch = []

        if batch:
            await target[name].insert_many(batch)
            total += len(batch)

        print(f'  {name}: {total}')

    print(f'\n✅ Готово. Пропишите в .env тестового бота: MONGO_DB={target_name}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Копия базы для тестов')
    parser.add_argument('--from', dest='source', required=True, help='боевая база, например RS_2')
    parser.add_argument('--to', dest='target', required=True, help='тестовая база, например RS_2_test')
    parser.add_argument('--uri', default='', help='строка подключения (по умолчанию из .env)')
    args = parser.parse_args()

    uri = args.uri
    if not uri:
        from app.core.config import Config
        uri = Config.from_env().mongo_uri

    return asyncio.run(clone(uri, args.source, args.target))


if __name__ == '__main__':
    sys.exit(main())
