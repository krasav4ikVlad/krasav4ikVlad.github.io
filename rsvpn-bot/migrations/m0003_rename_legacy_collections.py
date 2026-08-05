"""Переезд коллекций с пробелом в имени на нормальные имена.

`db['promo_codes ']` и `db['promo_codes']` — две РАЗНЫЕ коллекции. Пока код
писал строку с пробелом, данные копились в ней. Просто убрать пробел в коде
нельзя: бот начнёт читать пустую коллекцию, и промокоды с быстрыми ответами
«исчезнут».

Миграция копирует документы в правильные имена. Старые коллекции не удаляет —
чтобы был откат; удалить вручную после того, как всё поработает.
"""

from __future__ import annotations

import logging

from app.core.db import LEGACY_RENAMES

log = logging.getLogger(__name__)


async def up(container) -> None:
    if container.config.legacy_collections:
        # бот и так читает старые имена — копии создадут только путаницу
        log.info('m0003 пропущена: LEGACY_COLLECTIONS=1, работаем на старых именах')
        return

    for old_name, new_name in LEGACY_RENAMES.items():
        old = container.db[old_name]
        new = container.db[new_name]

        docs = await old.find({}).to_list(length=None)
        if not docs:
            continue

        moved = 0
        skipped = 0
        for doc in docs:
            # _id сохраняем: повторный прогон миграции ничего не задвоит
            if await new.find_one({'_id': doc['_id']}) is not None:
                continue
            try:
                await new.insert_one(doc)
                moved += 1
            except Exception as exc:
                # в старых данных мог оказаться дубль по уникальному индексу
                # (например два одинаковых промокода) — пропускаем документ,
                # но не обрываем перенос остальных
                skipped += 1
                log.warning('%r: документ %s не перенесён (%s)',
                            old_name, doc.get('_id'), exc)

        log.info('коллекция %r → %r: перенесено %s из %s%s',
                 old_name, new_name, moved, len(docs),
                 f', пропущено дублей: {skipped}' if skipped else '')
