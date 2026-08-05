"""Проверка окружения: что заполнено, что доступно, чего не хватает.

    python -m scripts.check_setup

Ничего не меняет — только читает. Запускать после заполнения .env и перед
первым стартом бота.
"""

from __future__ import annotations

import asyncio
import sys

# Ключи экранов: файл media/<ключ>.png (или .jpg) подставляется автоматически
SCREEN_IMAGES = ('profile', 'subscription', 'subscription_active', 'no_funds',
                 'devices', 'payment', 'referrals', 'gifts', 'bypass')

OK = '✅'
WARN = '⚠️ '
FAIL = '❌'


def line(status: str, name: str, detail: str = '') -> None:
    print(f'{status} {name}' + (f' — {detail}' if detail else ''))


async def main() -> int:
    from app.core.config import Config

    problems = 0
    print('\n── Конфигурация ─────────────────────────────────────────────')

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        line(FAIL, 'Конфиг', str(exc))
        print('\nЗаполните .env (см. .env.example) и запустите снова.')
        return 1

    line(OK, 'Токен бота', f'…{config.bot_token[-6:]}')
    line(OK if config.admin_ids else FAIL, 'ADMIN_IDS',
         ', '.join(map(str, config.admin_ids)) or 'не заданы — админка будет недоступна')
    problems += 0 if config.admin_ids else 1

    line(OK, 'База', f'{config.mongo_db} на {config.mongo_uri.split("@")[-1][:40]}')

    # ── Mongo ────────────────────────────────────────────────────────────────
    print('\n── База данных ──────────────────────────────────────────────')
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(config.mongo_uri, serverSelectionTimeoutMS=4000)
        await client.admin.command('ping')
        db = client[config.mongo_db]

        users = await db['users'].count_documents({})
        line(OK, 'Подключение к Mongo', f'пользователей: {users}')

        names = await db.list_collection_names()
        legacy = [n for n in names if n != n.strip()]
        if legacy:
            if config.legacy_collections:
                line(OK, 'Режим старых имён коллекций',
                     'включён — читаем те же коллекции, что старый бот')
            else:
                line(WARN, 'Коллекции с пробелом в имени',
                     ', '.join(repr(n) for n in legacy))
                print('     → либо `python -m migrations.runner` (перенос данных),')
                print('     → либо LEGACY_COLLECTIONS=1 в .env (работать как старый бот)')

        plans = await db['plans'].count_documents({})
        line(OK if plans else WARN, 'Тарифы',
             f'{plans} шт.' if plans else 'пусто — появятся при первом запуске')

        settings_count = await db['bot_settings'].count_documents({})
        line(OK, 'Изменённые настройки', f'{settings_count} шт. (остальные — по умолчанию)')

        # Уникальные индексы создаются миграцией. Если в данных уже есть
        # дубли, создание индекса упадёт — лучше узнать об этом заранее.
        for collection, field, title in (
            ('users', 'user_data.user_id', 'Пользователи'),
            ('promo_codes', 'code', 'Промокоды'),
            (' promo_codes', 'code', 'Промокоды (старая коллекция)'),
        ):
            source = collection.replace(' ', '') + ' ' if collection.startswith(' ') else collection
            if source not in names:
                continue

            # _id: null — это документы, где поля нет вообще. Дублями по
            # значению они не являются, а уникальный индекс строится только
            # по документам с полем, поэтому и ему они не мешают.
            duplicates = await db[source].aggregate([
                {'$group': {'_id': f'${field}', 'n': {'$sum': 1}}},
                {'$match': {'n': {'$gt': 1}, '_id': {'$ne': None}}},
                {'$limit': 5},
            ]).to_list(length=5)

            if duplicates:
                problems += 1
                line(FAIL, f'{title}: дубли по {field}',
                     ', '.join(str(d['_id']) for d in duplicates))
                print(f'     → выполните: python -m scripts.dedupe --collection {source!r}')
            else:
                line(OK, f'{title}: дублей нет', f'по полю {field}')

            missing = await db[source].count_documents({field: {'$exists': False}})
            if missing:
                line(OK, f'{title}: документов без поля', f'{missing} — индексу не мешают')

        # Индекс либо есть, либо нет — это надёжнее любых догадок
        try:
            indexes = await db['users'].index_information()
            spec = indexes.get('user_data.user_id_1') or {}
            if spec.get('unique'):
                line(OK, 'Уникальный индекс на user_data.user_id',
                     'есть' + (' (только по документам с полем)'
                               if spec.get('partialFilterExpression') else ''))
            else:
                line(WARN, 'Уникальный индекс на user_data.user_id', 'не создан')
                print('     → python -m migrations.runner --only m0001 --redo m0001')
        except Exception as exc:
            line(WARN, 'Индексы users', str(exc)[:80])
    except Exception as exc:
        line(FAIL, 'Подключение к Mongo', str(exc)[:120])
        problems += 1

    # ── Панель ───────────────────────────────────────────────────────────────
    print('\n── Панель Remnawave ─────────────────────────────────────────')
    if not config.vpn.base_url or not config.vpn.token:
        line(FAIL, 'API_URL / REMNAWAVE_TOKEN', 'не заданы — подписки создаваться не будут')
        problems += 1
    else:
        line(OK, 'Адрес панели', config.vpn.base_url)
        line(OK if config.vpn.webhook_secret else WARN, 'REMNAWAVE_WEBHOOK_SECRET',
             'задан' if config.vpn.webhook_secret
             else 'пуст — вебхуки принимаются без проверки подписи')

    # ── Платёжки ─────────────────────────────────────────────────────────────
    print('\n── Платёжные системы ────────────────────────────────────────')
    from app.integrations.payments.registry import build_providers

    providers = build_providers(config)
    if not providers:
        line(FAIL, 'Провайдеры', 'ни одного ключа в .env — пополнить баланс будет нечем')
        problems += 1
    for provider in providers:
        line(OK if provider.verified else WARN, provider.code,
             'подпись проверяется' if provider.verified
             else 'вебхук без подписи — принимается любой запрос')

    print('\n── Картинки экранов ─────────────────────────────────────────')
    from app.core.container import Container

    import os

    container = Container(config=config, db=db)
    found, missing = [], []
    for key in SCREEN_IMAGES:
        path = container.media(key)
        if path:
            found.append(f'{key} → {os.path.basename(path)}')
        else:
            missing.append(key)

    if found:
        line(OK, f'Найдено в {config.media_dir}/', '')
        for item in found:
            print(f'     {item}')
    else:
        line(WARN, f'В {config.media_dir}/ картинок не найдено', 'экраны уйдут текстом')

    if missing:
        line(OK, 'Без картинки (уйдут текстом)', ', '.join(missing))

    print('\n── Режим работы ─────────────────────────────────────────────')
    line(OK if config.vpn.dry_run else WARN, 'Панель',
         'только чтение (dry-run)' if config.vpn.dry_run else 'запросы на изменение уходят')
    line(OK, 'Коллекции',
         'старые имена, как у текущего бота' if config.legacy_collections else 'новые имена')

    if config.scheduler_enabled:
        line(WARN, 'Планировщик', 'включён — будет списывать деньги и слать кампании')
        print('     → пока работает старый бот, поставьте SCHEDULER_ENABLED=0,')
        print('       иначе списания и рассылки пойдут дважды')
    else:
        line(OK, 'Планировщик', 'выключен — списаний и рассылок не будет')

    print(f'\n{"─" * 62}')
    if problems:
        print(f'{FAIL} Проблем, мешающих запуску: {problems}')
    else:
        print(f'{OK} Всё готово. Запускайте: make migrate, затем make run')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
