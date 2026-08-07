"""Готова ли БОЕВАЯ база к новому боту — без единого изменения в ней.

    python -m scripts.check_compat

Скрипт только читает. Он отвечает на один вопрос: что будет, если запустить
новый бот на существующей базе как есть.

Проверяется три вещи:
  1. коллекции — те ли имена, что ждёт бот при текущем LEGACY_COLLECTIONS;
  2. поля — какие из тех, что бот читает, в документах отсутствуют, и чем
     это обернётся (обычно «покажет ноль», а не «упадёт»);
  3. последствия — сколько существующих пользователей получит доступ к
     новым возможностям, которых при старом боте не было.

Третий пункт — главный. Всё остальное чинится настройками, а вот раздача
бесплатных периодов десяткам тысяч старых пользователей — нет.
"""

from __future__ import annotations

import asyncio
import sys

OK = '✅'
WARN = '⚠️ '
FAIL = '❌'
INFO = 'ℹ️ '


def line(status: str, name: str, detail: str = '') -> None:
    print(f'{status} {name}' + (f' — {detail}' if detail else ''))


async def count(col, query: dict) -> int:
    try:
        return await col.count_documents(query)
    except Exception as exc:
        print(f'{FAIL} не удалось посчитать {query}: {exc}')
        return -1


async def main() -> int:
    from app.core import db as names
    from app.core.config import Config

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        line(FAIL, 'Конфиг', str(exc))
        return 1

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(config.mongo_uri, tz_aware=True,
                                serverSelectionTimeoutMS=8000)
    db = client[config.mongo_db]

    try:
        existing = set(await db.list_collection_names())
    except Exception as exc:
        line(FAIL, 'Подключение к базе', str(exc))
        return 1

    print(f'\n── База {config.mongo_db} ─────────────────────────────────────')
    line(INFO, 'Режим коллекций',
         'LEGACY_COLLECTIONS=1 — старые имена, как у текущего бота'
         if config.legacy_collections else
         'новые имена (LEGACY_COLLECTIONS не задан)')

    # ── 1. коллекции ────────────────────────────────────────────────────────
    print('\n── Коллекции ────────────────────────────────────────────────')
    problems = 0
    for logical in (names.QUICK_REPLIES, names.CHURN_SURVEYS,
                    names.PROMO_CODES, names.PROMO_USAGES):
        wanted = names.collection_name(logical, config.legacy_collections)
        other = names.LEGACY_BY_NEW.get(logical) if not config.legacy_collections \
            else logical
        if wanted in existing:
            line(OK, wanted, f'{await count(db[wanted], {})} документов')
        elif other in existing:
            problems += 1
            line(FAIL, wanted, f'нет, но есть «{other}» — данные лежат не там, '
                               f'куда посмотрит бот. Переключите LEGACY_COLLECTIONS')
        else:
            line(INFO, wanted, 'пусто с обеих сторон — создастся при первом обращении')

    for required in (names.USERS, names.PLANS):
        line(OK if required in existing else INFO, required,
             f'{await count(db[required], {})} документов' if required in existing
             else 'будет создана')

    users = db[names.USERS]
    total = await count(users, {})

    # ── 2. поля ─────────────────────────────────────────────────────────────
    print('\n── Поля, которые читает бот ─────────────────────────────────')
    checks = [
        ('growth.segment', 'сегменты', 'кампании и статистика пустые до первого '
                                       'прогона задачи сегментов (через 5 минут после старта)'),
        ('growth.has_topup', 'признак пополнения',
         '«Активных с пополнением» в /admin занижено до того же момента'),
        ('info.ref_stats.earned_total', 'заработано по рефералам',
         'в реферальном меню будет 0, пока не пройдут новые оплаты'),
        ('info.ref_stats.turnover_total', 'оборот рефералов',
         'средний чек покажет 0, пока не пройдут новые оплаты'),
        ('vpn.uuid', 'uuid подписки в панели',
         'у этих пользователей не сработают менеджер устройств и жёсткий бан'),
    ]
    for path, title, effect in checks:
        missing = await count(users, {path: {'$exists': False}})
        if missing <= 0:
            line(OK, title, 'есть у всех')
            continue
        share = f'{missing} из {total}'
        # отсутствие vpn.uuid у тех, кто не покупал, — норма
        if path == 'vpn.uuid':
            no_sub = await count(users, {'vpn.uuid': {'$exists': False},
                                         'vpn.shortUuid': {'$in': ['', None]}})
            if no_sub == missing:
                line(OK, title, 'нет только у тех, кто ни разу не покупал — это норма')
                continue
        line(WARN, title, f'нет у {share}: {effect}')

    # ── 3. последствия ──────────────────────────────────────────────────────
    print('\n── Что изменится для существующих пользователей ─────────────')

    eligible = await count(users, {
        'growth.trial_claimed_at': {'$exists': False},
        '$or': [{'vpn.shortUuid': {'$exists': False}}, {'vpn.shortUuid': ''}],
    })
    if eligible > 0:
        problems += 1
        line(WARN, 'Бесплатный период',
             f'его сможет забрать {eligible} существующих пользователей')
        print('     Это те, кто зарегистрирован при старом боте и ни разу не')
        print('     покупал подписку. Для них кнопка «3 дня бесплатно» — новая')
        print('     возможность, и каждый создаст подписку в панели.')
        print('     Решите заранее:')
        print('       • оставить как есть — это реактивация спящих;')
        print('       • закрыть для старых: python -m scripts.close_trial --apply')
        print('       • выключить совсем: /admin → Функции → Бесплатный период')
    else:
        line(OK, 'Бесплатный период', 'старым пользователям не достанется')

    banned = await count(users, {'moderation.banned': True})
    line(OK, 'Блокировки', f'сейчас заблокировано: {max(banned, 0)}')

    balance_users = await count(users, {'info.balance': {'$gt': 0}})
    line(INFO, 'Балансы',
         f'у {balance_users} пользователей есть деньги — они сохранятся, '
         'новым баланс больше не начисляется')

    # ── что бот допишет ─────────────────────────────────────────────────────
    print('\n── Что бот добавит в базу ───────────────────────────────────')
    print('   Только новые поля и коллекции, ничего не переименовывая:')
    print('     • moderation.*            — блокировки')
    print('     • growth.trial_claimed_at — отметка о бесплатном периоде')
    print('     • info.ref_stats.method   — реквизиты для вывода')
    print('     • bot_settings, media_cache, campaign_runs — новые коллекции')
    print('   Старый бот этих полей не читает, поэтому может работать рядом.')

    print()
    if problems:
        print(f'{WARN} Требует решения пунктов: {problems}. База при этом рабочая.')
    else:
        print(f'{OK} База готова: можно запускать без изменений в ней.')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
