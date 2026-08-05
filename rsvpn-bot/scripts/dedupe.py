"""Поиск и удаление дублей.

    python -m scripts.dedupe                      # отчёт, ничего не меняет
    python -m scripts.dedupe --apply              # удалить безопасные дубли
    python -m scripts.dedupe --apply --force      # удалить и спорные тоже
    python -m scripts.dedupe --collection "promo_codes " --field code

Оставляется самая ранняя запись — та же, которую возвращает find_one.
Удалённые документы копируются в коллекцию <название>_dupes_backup,
поэтому вернуть их можно в любой момент.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.services.dedupe import DedupeService, pick


def short(doc: dict) -> str:
    """Короткая сводка документа, чтобы решение принималось глазами."""
    parts = [f"_id={doc.get('_id')}"]
    for path, label in (('info.balance', 'баланс'), ('vpn.shortUuid', 'подписка'),
                        ('user_data.date_joined', 'создан')):
        value = pick(doc, path)
        if value not in (None, '', 0):
            parts.append(f'{label}={value}')
    transactions = pick(doc, 'info.transactions')
    if transactions:
        parts.append(f'транзакций={len(transactions)}')
    return ', '.join(str(p) for p in parts)


async def main(args) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core.config import Config

    config = Config.from_env()
    db = AsyncIOMotorClient(config.mongo_uri, tz_aware=True)[config.mongo_db]

    backup_name = f'{args.collection.strip()}_dupes_backup'
    service = DedupeService(db[args.collection], db[backup_name])

    print(f'База: {config.mongo_db}, коллекция: {args.collection!r}, '
          f'поле: {args.field}')
    print('Ищу дубли — группировка идёт на сервере, документы не выкачиваются…\n')

    def progress(done, total=None):
        print(f'   … обработано {done}' + (f' из {total}' if total else ''), flush=True)

    report = await service.scan(args.field, progress=progress)

    print(f'Просмотрено документов: {report.scanned}')
    print(f'Групп с дублями:        {len(report.groups)}')
    print(f'Лишних документов:      {report.extra_documents}')
    print(f'  из них безопасных:    {sum(len(g.remove) for g in report.safe)}')
    print(f'  спорных:              {sum(len(g.remove) for g in report.risky)}\n')

    for group in report.risky[:args.limit]:
        print(f'⚠️  {args.field}={group.key} — в дублях есть данные, которых нет '
              f'в оставляемой записи:')
        print(f'      оставить: {short(group.keep)}')
        for doc in group.remove:
            print(f'      удалить:  {short(doc)}')
        print(f'      различия: {", ".join(group.conflicts)}\n')

    if report.risky and len(report.risky) > args.limit:
        print(f'   … и ещё {len(report.risky) - args.limit} спорных групп\n')

    if not args.apply:
        print('Ничего не изменено. Для удаления добавьте --apply')
        if report.risky:
            print('Спорные группы удаляются только с --apply --force')
        return 0

    deleted = await service.delete(report, include_risky=args.force)
    print(f'✅ Удалено: {deleted}')
    print(f'   Копии удалённых: коллекция {backup_name}')
    if report.risky and not args.force:
        print(f'   Спорные группы ({len(report.risky)}) не тронуты — '
              f'разберите их вручную или запустите с --force')
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(description='Удаление дублей')
    parser.add_argument('--collection', default='users')
    parser.add_argument('--field', default='user_data.user_id')
    parser.add_argument('--apply', action='store_true', help='выполнить удаление')
    parser.add_argument('--force', action='store_true', help='удалять и спорные группы')
    parser.add_argument('--limit', type=int, default=10, help='сколько спорных групп показать')
    return asyncio.run(main(parser.parse_args()))


if __name__ == '__main__':
    sys.exit(cli())
