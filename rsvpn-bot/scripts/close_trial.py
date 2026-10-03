"""Закрыть бесплатный период для тех, кто был до его появления.

    python -m scripts.close_trial            # только посчитать
    python -m scripts.close_trial --apply    # проставить отметку

Зачем: кнопка «3 дня бесплатно» появилась вместе с новым ботом, а условие
у неё простое — «ещё не забирал и нет подписки». Под это подходят все, кто
зарегистрировался при старом боте и ничего не купил. Их могут быть десятки
тысяч, и каждый создаст подписку в панели.

Скрипт ставит им отметку «уже получал», как будто период уже забран. Ничего
не удаляет и не переименовывает: добавляется одно поле
growth.trial_claimed_at. Старый бот его не читает.

Новые пользователи, зарегистрированные после запуска, отметку не получат —
у них кнопка останется.
"""

from __future__ import annotations

import asyncio
import sys

QUERY = {'growth.trial_claimed_at': {'$exists': False}}


async def main() -> int:
    from app.core import db as names
    from app.core.config import Config
    from app.core.time import now

    apply = '--apply' in sys.argv
    config = Config.from_env()

    from motor.motor_asyncio import AsyncIOMotorClient

    db = AsyncIOMotorClient(config.mongo_uri, tz_aware=True)[config.mongo_db]
    users = db[names.USERS]

    total = await users.count_documents({})
    without_mark = await users.count_documents(QUERY)
    would_claim = await users.count_documents({
        **QUERY,
        '$or': [{'vpn.shortUuid': {'$exists': False}}, {'vpn.shortUuid': ''}],
    })

    print(f'\nВсего пользователей:            {total}')
    print(f'Без отметки о бесплатном периоде: {without_mark}')
    print(f'Из них СМОГУТ его забрать:        {would_claim}')
    print('  (нет подписки — значит кнопка им покажется)\n')

    if not apply:
        print('Это была проверка. Чтобы закрыть период для всех существующих:')
        print('    python -m scripts.close_trial --apply\n')
        print('Оставить как есть тоже нормально: для спящих это реактивация.')
        return 0

    result = await users.update_many(
        QUERY, {'$set': {'growth.trial_claimed_at': now(),
                         'growth.trial_closed_by_script': True}})
    print(f'Отметка проставлена: {result.modified_count} документов.')
    print('Бесплатный период теперь доступен только новым пользователям.')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
