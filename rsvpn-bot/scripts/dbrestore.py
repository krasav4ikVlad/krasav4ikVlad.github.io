"""Восстановление базы из снимка.

    python -m scripts.dbrestore ~/rsvpn-backups/RS_2-2026-09-30-0400.jsonl.gz
    python -m scripts.dbrestore файл --into RS_2_test
    python -m scripts.dbrestore файл --drop --yes

Без аргументов, кроме файла, — только показывает, что внутри, и ничего не
меняет. Это нарочно: команда восстановления, которая что-то делает сразу,
однажды будет набрана не в том окне.

Что делает с ключами: документы кладутся по своему `_id` (replace + upsert),
поэтому повторный запуск не плодит дубликатов, а существующие документы
перезаписываются снимком. Лишнее, чего в снимке нет, остаётся на месте —
если нужно вернуть базу ровно к состоянию снимка, добавьте `--drop`.

Живую базу трогаем только с `--yes`: без него скрипт откажется писать в ту
базу, с которой работает бот.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import sys
from collections import Counter
from pathlib import Path

BATCH = 500


def read_lines(path: Path):
    from bson.json_util import loads

    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = loads(line)
            except Exception as exc:      # noqa: BLE001 — строку назовём вслух
                raise RuntimeError(f'строка {number} не разобралась: {exc}') from exc
            if not isinstance(row, dict) or 'c' not in row or 'd' not in row:
                raise RuntimeError(f'строка {number} не похожа на снимок')
            yield row['c'], row['d']


def inspect(path: Path) -> Counter:
    counts: Counter = Counter()
    for name, _ in read_lines(path):
        counts[name] += 1
    return counts


def replace_op(doc: dict):
    """Операция «положить документ целиком по его _id».

    Именно замена, а не $set: поля, которых в снимке нет, должны исчезнуть,
    иначе восстановление оставляет мусор, появившийся после снимка.

    Импорт внутри — чтобы разбор файла (`--dry`) работал и там, где драйвер
    не поднимается.
    """
    from pymongo import ReplaceOne

    return ReplaceOne({'_id': doc['_id']}, doc, upsert=True)


async def write_docs(db, path: Path, *, drop: bool) -> Counter:
    """Залить снимок в базу. Возвращает, сколько куда легло.

    Отдельно от main(), чтобы это можно было проверить тестом: это та
    самая половина, которая работает раз в жизни и должна сработать.
    """
    dropped: set[str] = set()
    batches: dict[str, list] = {}
    written: Counter = Counter()

    async def flush(collection: str) -> None:
        rows = batches.pop(collection, [])
        if rows:
            await db[collection].bulk_write(rows, ordered=False)
            written[collection] += len(rows)

    for collection, doc in read_lines(path):
        if drop and collection not in dropped:
            await db[collection].drop()
            dropped.add(collection)
        batches.setdefault(collection, []).append(replace_op(doc))
        if len(batches[collection]) >= BATCH:
            await flush(collection)

    for collection in list(batches):
        await flush(collection)
    return written


async def restore(path: Path, uri: str, name: str, *, drop: bool) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    db = AsyncIOMotorClient(uri, tz_aware=True)[name]
    written = await write_docs(db, path, drop=drop)

    print(f'\nВосстановлено в базу {name}:')
    for collection, count in sorted(written.items()):
        print(f'  {collection}: {count}')
    print(f'\n✅ Всего документов: {sum(written.values())}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Восстановление базы из снимка')
    parser.add_argument('file', help='файл снимка (.jsonl.gz)')
    parser.add_argument('--into', default='', help='база-приёмник (по умолчанию из .env)')
    parser.add_argument('--uri', default='', help='адрес Mongo (по умолчанию из .env)')
    parser.add_argument('--drop', action='store_true',
                        help='очистить коллекции перед записью')
    parser.add_argument('--yes', action='store_true',
                        help='подтвердить запись (без него — только показать)')
    args = parser.parse_args()

    path = Path(args.file).expanduser()
    if not path.exists():
        print(f'❌ Нет файла: {path}')
        return 1

    from app.core.config import Config

    config = Config.from_env()
    uri = args.uri or config.mongo_uri
    name = args.into or config.mongo_db
    live = (not args.into or args.into == config.mongo_db) and not args.uri

    print(f'Снимок: {path.name}')
    counts = inspect(path)
    for collection, count in sorted(counts.items()):
        print(f'  {collection}: {count}')
    print(f'  ── всего: {sum(counts.values())}')

    if not args.yes:
        print('\nЭто только просмотр, база не тронута.')
        print(f'Восстановить в базу {name}:')
        print(f'   python -m scripts.dbrestore {args.file}'
              + (f' --into {args.into}' if args.into else '')
              + ' --yes'
              + ('  # добавьте --drop, чтобы вернуть базу ровно к снимку'
                 if not args.drop else ' --drop'))
        return 0

    if live:
        print(f'\n⚠️  Пишем в БОЕВУЮ базу {name}.')
        print('   Остановите бота перед восстановлением:')
        print('   pm2 stop rsvpn-bot rsvpn-api')
        answer = input(f'\nВведите имя базы «{name}» для подтверждения: ').strip()
        if answer != name:
            print('Отменено.')
            return 1

    return asyncio.run(restore(path, uri, name, drop=args.drop))


if __name__ == '__main__':
    sys.exit(main())
