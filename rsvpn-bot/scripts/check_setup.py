"""Проверка окружения: что заполнено, что доступно, чего не хватает.

    python -m scripts.check_setup

Ничего не меняет — только читает. Запускать после заполнения .env и перед
первым стартом бота.
"""

from __future__ import annotations

import asyncio
import sys

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
            line(WARN, 'Коллекции с пробелом в имени', ', '.join(repr(n) for n in legacy))
            print('     → выполните `make migrate`, данные перенесутся в правильные имена')

        plans = await db['plans'].count_documents({})
        line(OK if plans else WARN, 'Тарифы',
             f'{plans} шт.' if plans else 'пусто — появятся при первом запуске')

        settings_count = await db['bot_settings'].count_documents({})
        line(OK, 'Изменённые настройки', f'{settings_count} шт. (остальные — по умолчанию)')
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

    print(f'\n{"─" * 62}')
    if problems:
        print(f'{FAIL} Проблем, мешающих запуску: {problems}')
    else:
        print(f'{OK} Всё готово. Запускайте: make migrate, затем make run')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
