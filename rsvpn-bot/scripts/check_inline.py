"""Почему не появляется меню подарков при упоминании бота в чате.

    python -m scripts.check_inline

Инлайн-режим — это не код бота, а флаг на стороне Telegram. Пока он снят,
сервер вообще не отправляет боту inline_query: обработчику нечего
обрабатывать, и в логах ничего не появляется. Отсюда и ощущение, что «бот
не реагирует».

Скрипт спрашивает у самого Telegram, стоит ли этот флаг у токена из .env,
и заодно показывает, какие типы обновлений запрашивает диспетчер, — так
видно, на чьей стороне проблема. Ничего не меняет.
"""

from __future__ import annotations

import asyncio
import sys

OK = '✅'
FAIL = '❌'


async def main() -> int:
    from aiogram import Bot

    from app.core.config import Config

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        print(f'{FAIL} Конфиг: {exc}')
        return 1

    bot = Bot(token=config.bot_token)
    try:
        me = await bot.get_me()
    except Exception as exc:
        print(f'{FAIL} Telegram не ответил на getMe: {exc}')
        return 1
    finally:
        await bot.session.close()

    print('\n── Бот ──────────────────────────────────────────────────────')
    print(f'   Юзернейм: @{me.username}  (id {me.id})')

    # ── сторона кода ────────────────────────────────────────────────────────
    from app.bot.factory import create_dispatcher
    from app.core.container import Container

    container = Container(config=config, db=_OfflineDB())
    updates = sorted(create_dispatcher(container).resolve_used_update_types())

    print('\n── Сторона бота ─────────────────────────────────────────────')
    inline_wanted = 'inline_query' in updates
    print(f'{OK if inline_wanted else FAIL} Запрашиваемые обновления: {", ".join(updates)}')
    if not inline_wanted:
        print('   Обработчик inline_query не зарегистрирован — это уже вопрос к коду.')

    # ── сторона Telegram ────────────────────────────────────────────────────
    print('\n── Сторона Telegram ─────────────────────────────────────────')
    if me.supports_inline_queries:
        print(f'{OK} Инлайн-режим включён у @{me.username}')
        print('\nОбе стороны в порядке. Если меню всё равно не появляется:')
        print('  • полностью перезапустите клиент Telegram — список ботов,')
        print('    поддерживающих инлайн, он кэширует;')
        print('  • проверьте, что в чате набираете именно @' + (me.username or ''),
              '\n    (у тестового и боевого бота разные юзернеймы);')
        print('  • в группах инлайн может быть запрещён настройками группы.')
        return 0

    print(f'{FAIL} Инлайн-режим ВЫКЛЮЧЕН у @{me.username}')
    print('\nЭто и есть причина: Telegram не отправляет боту запрос, пока флаг снят.')
    print('Никакая правка кода на это не влияет. Включается так:')
    print('  1. Откройте @BotFather')
    print('  2. Отправьте /setinline')
    print(f'  3. Выберите @{me.username}')
    print('  4. Пришлите любой текст подсказки, например: выберите подарок')
    print('  5. Перезапустите клиент Telegram и попробуйте снова')
    print('\nФлаг ставится для каждого бота отдельно: включённый у боевого бота')
    print('инлайн не работает у тестового.')
    return 1


class _OfflineDB(dict):
    """Заглушка базы: скрипту нужен только собранный диспетчер, не данные."""

    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, _OfflineCollection())
        return super().__getitem__(name)


class _OfflineCollection:
    def __getattr__(self, _):
        raise RuntimeError('скрипт не обращается к базе')


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
