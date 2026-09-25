"""Блокировка пользователей.

Флаг лежит в самом документе пользователя (`moderation.banned`), а не в
отдельной коллекции: он нужен на КАЖДОМ обновлении, а документ и так
читается middleware — лишнего запроса не появляется.

Уровня два: обычный закрывает только бота, жёсткий вдобавок отключает
подписки в панели. Деньги не трогает ни один — баланс остаётся на месте,
разбан возвращает всё как было.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.time import now

log = logging.getLogger(__name__)


@dataclass
class BanResult:
    ok: bool
    hard: bool = False
    disabled: int = 0          # сколько подписок отключено в панели
    panel_failed: int = 0      # сколько отключить не удалось


class ModerationService:
    """Два уровня блокировки.

    Обычный бан закрывает только бота: человек не может ничего нажать, но
    его конфиг продолжает работать до конца оплаченного срока. Это верно
    для «надоел в поддержке» и обратимо без последствий.

    Жёсткий бан вдобавок отключает подписки в панели — доступ пропадает
    сразу. Нужен там, где дело не в общении: возвраты по картам, перепродажа
    доступа, злоупотребление. Разбан включает подписки обратно.
    """

    def __init__(self, users, settings, vpn=None):
        self.users = users
        self.settings = settings
        self.vpn = vpn

    @staticmethod
    def is_banned(user: dict | None) -> bool:
        return bool(((user or {}).get('moderation') or {}).get('banned'))

    @staticmethod
    def is_hard(user: dict | None) -> bool:
        return bool(((user or {}).get('moderation') or {}).get('hard'))

    async def ban(self, user_id: int, admin_id: int, reason: str = '',
                  hard: bool = False) -> BanResult:
        """Забанить. hard=True — ещё и отключить подписки в панели."""
        user = await self.users.get(user_id)
        if not user:
            return BanResult(False)

        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'moderation.banned': True, 'moderation.hard': bool(hard),
                      'moderation.banned_at': now(), 'moderation.banned_by': admin_id,
                      'moderation.reason': reason}},
        )
        log.info('забанен %s (%s) админом %s: %s', user_id,
                 'жёстко' if hard else 'обычно', admin_id, reason or 'без причины')

        if not hard:
            return BanResult(True)

        disabled, failed = await self._set_status(user, 'DISABLED')
        return BanResult(True, hard=True, disabled=disabled, panel_failed=failed)

    async def unban(self, user_id: int, admin_id: int) -> BanResult:
        """Снять любую блокировку. После жёсткой — вернуть подписки в строй."""
        user = await self.users.get(user_id)
        if not user:
            return BanResult(False)

        was_hard = self.is_hard(user)
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'moderation.banned': False, 'moderation.hard': False,
                      'moderation.unbanned_at': now(),
                      'moderation.unbanned_by': admin_id}},
        )
        log.info('разбанен %s админом %s', user_id, admin_id)

        if not was_hard:
            return BanResult(True)

        restored, failed = await self._set_status(user, 'ACTIVE')
        return BanResult(True, hard=True, disabled=restored, panel_failed=failed)

    async def _set_status(self, user: dict, status: str) -> tuple[int, int]:
        """Переключить обе подписки — основную и ByPass. (успешно, с ошибкой).

        Отказ панели не откатывает блокировку: в базе человек уже забанен и
        до бота не доходит. Незакрытая подписка — это то, что нужно повторить,
        а не повод считать бан несостоявшимся.
        """
        if self.vpn is None:
            return 0, 0

        done = failed = 0
        for field in ('vpn.uuid', 'vpn.bypass_uuid'):
            uuid = self.users.pick(user, field)
            if not uuid:
                continue
            try:
                await self.vpn.set_status(uuid, status)
                done += 1
            except Exception as exc:
                failed += 1
                log.error('подписка %s не переведена в %s: %s', uuid, status, exc)
        return done, failed

    async def banned(self, limit: int = 50) -> list[dict]:
        return await self.users.col.find(
            {'moderation.banned': True},
            {'user_data.user_id': 1, 'user_data.username': 1, 'moderation': 1},
        ).limit(limit).to_list(length=limit)

    async def count(self) -> int:
        return await self.users.col.count_documents({'moderation.banned': True})

    async def find_user(self, query: str) -> dict | None:
        """Пользователь по id или юзернейму — чтобы банить не только по цифрам."""
        query = query.strip().lstrip('@')
        if query.isdigit():
            return await self.users.get(int(query))
        return await self.users.col.find_one({'user_data.username': query})
