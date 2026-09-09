"""Копия базы для тестового контура.

    python -m scripts.clone_db --from RS_2 --to RS_2_test
    python -m scripts.clone_db --from RS_2 --to RS_2 --to-uri mongodb://localhost:27017

Читает исходную базу и складывает документы в целевую. Исходную не трогает
вообще: только find(). Целевая перед копированием очищается — поэтому скрипт
отказывается работать, если совпадают и база, и адрес.

Целевая база может жить на другом сервере (--to-uri). Это нужно, когда у
пользователя Mongo есть права только на боевую базу: тогда копию кладут в
локальный Mongo, а боевой кластер остаётся нетронутым.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

BATCH = 500
SYSTEM_PREFIX = 'system.'


async def _check_writable(target, target_name: str) -> bool:
    """Проверяем права ДО копирования, чтобы не падать в середине."""
    probe = target['__clone_probe']
    try:
        await probe.insert_one({'_id': 'probe'})
        await probe.drop()
        return True
    except Exception as exc:
        message = str(exc)
        print(f'❌ Нет прав на запись в базу {target_name}.\n')
        if 'not authorized' in message or 'Unauthorized' in message:
            print('   Пользователь Mongo имеет доступ только к боевой базе.')
            print('   Варианты:\n')
            print('   1) Выдать права на тестовую базу.')
            print('      MongoDB Atlas: Database Access → Edit user → Add role')
            print(f'      → Specific Privilege: readWrite на {target_name}.\n')
            print('   2) Скопировать в локальный Mongo и не трогать боевой кластер:')
            print('      docker run -d --name mongo-test -p 27017:27017 mongo:7')
            print('      python -m scripts.clone_db --from RS_2 --to RS_2_test '
                  '--to-uri mongodb://localhost:27017\n')
        else:
            print(f'   {message[:300]}\n')
        return False


async def clone(source_uri: str, source_name: str,
                target_uri: str, target_name: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    if source_name == target_name and source_uri == target_uri:
        print('❌ Исходная и целевая база совпадают — так копировать нельзя.')
        return 1

    source = AsyncIOMotorClient(source_uri, tz_aware=True)[source_name]
    target = AsyncIOMotorClient(target_uri, tz_aware=True)[target_name]

    if not await _check_writable(target, target_name):
        return 1

    names = [n for n in await source.list_collection_names()
             if not n.startswith(SYSTEM_PREFIX)]
    print(f'Копирую {len(names)} коллекций: {source_name} → {target_name}\n')

    total_docs = 0
    for name in names:
        await target[name].drop()
        copied = 0
        batch: list[dict] = []

        async for doc in source[name].find({}):
            batch.append(doc)
            if len(batch) >= BATCH:
                await target[name].insert_many(batch)
                copied += len(batch)
                batch = []

        if batch:
            await target[name].insert_many(batch)
            copied += len(batch)

        total_docs += copied
        print(f'  {name}: {copied}')

    print(f'\n✅ Скопировано документов: {total_docs}')
    print(f'   В .env тестового бота: MONGO_DB={target_name}')
    if target_uri != source_uri:
        print(f'   и TOKEN_DB={target_uri}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Копия базы для тестов')
    parser.add_argument('--from', dest='source', required=True, help='боевая база, например RS_2')
    parser.add_argument('--to', dest='target', required=True, help='тестовая база, например RS_2_test')
    parser.add_argument('--uri', default='', help='адрес источника (по умолчанию из .env)')
    parser.add_argument('--to-uri', dest='target_uri', default='',
                        help='адрес приёмника, если он на другом сервере')
    args = parser.parse_args()

    source_uri = args.uri
    if not source_uri:
        from app.core.config import Config
        source_uri = Config.from_env().mongo_uri

    return asyncio.run(clone(source_uri, args.source,
                             args.target_uri or source_uri, args.target))


if __name__ == '__main__':
    sys.exit(main())
