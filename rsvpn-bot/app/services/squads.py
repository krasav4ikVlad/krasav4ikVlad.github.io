"""Сквады Remnawave: базовый, ротация и «отпечаток».

Логика перенесена из utils.py как есть, но списки сквадов больше не зашиты
в код — они лежат в настройках, поэтому добавить или убрать сервер можно
из админки, а не деплоем.

Счётчик ротации хранится в Mongo (settings_collection), чтобы раскладка была
общей для всех процессов: и бот, и API выдают разные extra-сквады по кругу.
"""

from __future__ import annotations

import logging
from math import comb

from app.core.time import now

log = logging.getLogger(__name__)

ROTATION_DOC_ID = 'squad_rotation'
FINGERPRINT_DOC_ID = 'fingerprint_combo_counter'


def build_active_squads(base_squad: str, extra_squad: str | None,
                        existing: list[str] | None = None) -> list[str]:
    """Базовый + ротационный + уже выданные, без повторов и с сохранением порядка."""
    result: list[str] = []
    seen: set[str] = set()

    for squad in (base_squad, extra_squad, *(existing or [])):
        if squad and squad not in seen:
            seen.add(squad)
            result.append(squad)
    return result


def unrank_combination(n: int, k: int, rank: int) -> list[int]:
    """Комбинация номер `rank` из C(n, k) — без перебора и без повторов."""
    result: list[int] = []
    x = 0
    while k > 0:
        count = comb(n - x - 1, k - 1)
        if rank < count:
            result.append(x)
            k -= 1
        else:
            rank -= count
        x += 1
    return result


class SquadService:
    def __init__(self, settings, state_collection, fingerprints_collection=None):
        self.settings = settings
        self.state = state_collection
        self.fingerprints = fingerprints_collection

    async def base_squad(self) -> str:
        return await self.settings.get('squads.base')

    async def extra_squads(self) -> list[str]:
        raw = await self.settings.get('squads.extra', '')
        return [s.strip() for s in str(raw).split(',') if s.strip()]

    async def next_extra_squad(self) -> str | None:
        """Следующий сквад по кругу. Счётчик общий на все процессы."""
        squads = await self.extra_squads()
        if not squads:
            return None

        doc = await self.state.find_one_and_update(
            {'_id': ROTATION_DOC_ID},
            {'$inc': {'i': 1}},
            upsert=True,
            return_document=True,
        )
        index = (int((doc or {}).get('i', 1)) - 1) % len(squads)
        return squads[index]

    async def for_new_user(self) -> list[str]:
        return build_active_squads(await self.base_squad(), await self.next_extra_squad())

    async def for_existing_user(self, current: list[str] | None) -> list[str]:
        """Набор сквадов при продлении.

        Если ротационный сквад у человека уже есть — оставляем его, иначе
        выдаём следующий. Legacy-фингерпринты отсекаются, чтобы не вернулись
        обратно через existing.
        """
        current = current or []
        extra_pool = set(await self.extra_squads())
        fingerprints = set(await self.fingerprint_squads())

        current_extra = next((s for s in current if s in extra_pool), None) \
            or await self.next_extra_squad()
        cleaned = [s for s in current if s not in fingerprints]

        return build_active_squads(await self.base_squad(), current_extra, cleaned)

    # ── фингерпринты ────────────────────────────────────────────────────────
    async def fingerprint_squads(self) -> list[str]:
        raw = await self.settings.get('squads.fingerprint', '')
        return [s.strip() for s in str(raw).split(',') if s.strip()]

    async def assign_fingerprint(self, user_id: int, source: str = 'unknown') -> list[str]:
        """Уникальная комбинация сквадов на пользователя.

        Если комбинации закончились — не падаем, а логируем и переиспользуем
        по кругу: раньше здесь вылетал RuntimeError прямо в обработчике покупки,
        то есть у человека списывались деньги, а подписка не создавалась.
        """
        pool = await self.fingerprint_squads()
        pick = await self.settings.int('squads.fingerprint_pick')
        if not pool or pick <= 0 or self.fingerprints is None:
            return []

        existing = await self.fingerprints.find_one({'user_id': user_id})
        if existing and len(existing.get('squads', [])) == pick:
            return existing['squads']

        counter = await self.state.find_one_and_update(
            {'_id': FINGERPRINT_DOC_ID},
            {'$inc': {'value': 1}},
            upsert=True,
            return_document=True,
        )
        index = int((counter or {}).get('value', 1)) - 1

        total = comb(len(pool), pick)
        if index >= total:
            log.warning('комбинации фингерпринтов закончились (%s), идём по кругу', total)
            index %= total

        squads = [pool[i] for i in unrank_combination(len(pool), pick, index)]
        await self.fingerprints.update_one(
            {'user_id': user_id},
            {'$set': {'squads': squads, 'combo_index': index,
                      'source': source, 'updated_at': now()},
             '$setOnInsert': {'created_at': now()}},
            upsert=True,
        )
        return squads
