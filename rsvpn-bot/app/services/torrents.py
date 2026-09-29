"""Торренты: предупредить человека, повторного — отключить.

Плагин Torrent Blocker на ноде ловит bittorrent и закрывает IP на своё
время (`blockDuration` в его конфигурации). Для нас это событие важнее,
чем для него: из-за торрентов блокируют сервер целиком, и без доступа
остаются все, а не один.

Панель присылает это вебхуком `torrent_blocker.report` (с версии 2.7.0).
Мы его не создаём и не перепроверяем — только объясняем человеку, что
случилось, и считаем, сколько раз это было.

Что делаем:

  * первое нарушение — пишем в бота: торренты запрещены, доступ с этого
    адреса закрыт на N минут, при повторе подписка отключается;
  * повторное (порог в настройках) — отключаем подписки в панели навсегда
    и говорим об этом.

Чего не делаем: не закрываем бота. Человек должен видеть, за что, и уметь
написать в поддержку — и, если это была ошибка, получить доступ обратно.

Время блокировки берём из самого отчёта, а не из настроек бота: в конфиге
плагина оно меняется в один клик, и зашитое в текст число однажды станет
враньём.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.content import texts
from app.core.time import fmt, now, parse_dt

log = logging.getLogger(__name__)

EVENT = 'torrent_blocker.report'
REASON = 'торренты'

# Отчёт приходит не по одному: торрент-клиент за сессию даёт их пачкой.
# Окно тишины — чтобы человек получил одно сообщение на одну блокировку,
# а не сорок. Нарушением считается окно, а не отчёт.
DEFAULT_COOLDOWN_MIN = 30


class TorrentGuard:
    def __init__(self, users, settings, sender, bot, moderation, notifier=None):
        self.users = users
        self.settings = settings
        self.sender = sender
        self.bot = bot
        self.moderation = moderation
        self.notifier = notifier

    async def handle(self, data: dict) -> dict:
        if not await self.settings.flag('torrents.enabled'):
            return {'ok': True, 'note': 'disabled'}

        action = ((data.get('report') or {}).get('actionReport') or {})
        node = str((data.get('node') or {}).get('name') or '')
        panel_user = data.get('user') or {}

        user = await self._find_user(panel_user)
        if not user:
            # Не находим — значит подписка не наша (или чужая панель).
            # Строка в лог: молчание здесь неотличимо от «вебхук не дошёл».
            log.warning('торрент: пользователь панели %s не найден в базе',
                        panel_user.get('username') or panel_user.get('uuid'))
            return {'ok': True, 'note': 'user_not_found'}

        user_id = int(self.users.pick(user, 'user_data.user_id') or 0)
        if not user_id:
            return {'ok': True, 'note': 'no_user_id'}

        count = await self._strike(user_id, str(action.get('ip') or ''), node)
        if count is None:
            return {'ok': True, 'note': 'same_block', 'user_id': user_id}

        limit = int(await self.settings.int('torrents.block_after') or 0)
        blocked = bool(limit and count >= limit
                       and not self.moderation.vpn_locked(user))

        if blocked:
            await self.moderation.lock_vpn(user_id, f'{REASON}: {count}-е нарушение')
        delivered = await self._tell(user, user_id, count, action, blocked)
        await self._tell_admins(user_id, count, action, node, blocked)

        log.warning('торрент у %s: нарушение %s, нода %s, %s',
                    user_id, count, node or '?',
                    'подписка отключена' if blocked else 'предупреждён')
        return {'ok': True, 'user_id': user_id, 'count': count,
                'note': f'blocked_{count}' if blocked else f'warned_{count}',
                'delivered': delivered}

    # ── счёт нарушений ──────────────────────────────────────────────────────
    async def _strike(self, user_id: int, ip: str, node: str) -> int | None:
        """Засчитать нарушение. None — это та же блокировка, что и минуту назад.

        Окно закрывается атомарно самим update: панель ретраит вебхуки, а
        торрент-клиент даёт пачку отчётов, и оба случая должны стоить
        человеку ровно одного сообщения.
        """
        window = int(await self.settings.int('torrents.cooldown_min')
                     or DEFAULT_COOLDOWN_MIN)
        border = now() - timedelta(minutes=window)

        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             '$or': [{'moderation.torrent.last_at': {'$lt': border}},
                     {'moderation.torrent.last_at': None},
                     {'moderation.torrent.last_at': {'$exists': False}}]},
            {'$inc': {'moderation.torrent.count': 1,
                      'moderation.torrent.reports': 1},
             '$set': {'moderation.torrent.last_at': now(),
                      'moderation.torrent.last_ip': ip,
                      'moderation.torrent.last_node': node}})

        if getattr(claimed, 'modified_count', 0) != 1:
            # Отчёт всё равно считаем: по этому числу видно, качает человек
            # постоянно или один раз забыл выключить клиент.
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$inc': {'moderation.torrent.reports': 1}})
            return None

        fresh = await self.users.get(user_id, {'moderation.torrent.count': 1})
        return int(self.users.pick(fresh or {}, 'moderation.torrent.count', 0) or 0)

    # ── разговор ────────────────────────────────────────────────────────────
    async def _tell(self, user: dict, user_id: int, count: int, action: dict,
                    blocked: bool) -> bool:
        if not await self.settings.flag('torrents.warn_user'):
            return False

        seconds = int(action.get('blockDuration') or 0)
        text = texts.render(
            'torrent.blocked' if blocked else 'torrent.warning',
            name=self.users.pick(user, 'user_data.first_name'),
            minutes=max(1, round(seconds / 60)) if seconds else '',
            until=fmt(parse_dt(action.get('willUnblockAt')), '%H:%M'),
            count=count,
            support_url=await self.settings.get('link.support'),
        )
        return await self.sender.send(self.bot, user_id, text)

    async def _tell_admins(self, user_id: int, count: int, action: dict,
                           node: str, blocked: bool) -> None:
        if self.notifier is None:
            return
        try:
            await self.notifier.torrent(user_id, count=count, node=node,
                                        ip=str(action.get('ip') or ''),
                                        blocked=blocked)
        except Exception as exc:      # noqa: BLE001 — уведомление не главное
            log.warning('торрент: админ-уведомление не ушло: %s', exc)

    # ── поиск человека ──────────────────────────────────────────────────────
    async def _find_user(self, panel_user: dict) -> dict | None:
        """У нас имя пользователя в панели — это telegram id строкой.

        Поэтому сначала по нему, а потом по всему остальному, что панель
        прислала: у ByPass-подписки имя другое (`<id>_bypass`), а у старых
        записей могло не быть telegramId.
        """
        for field, value in (
            ('user_data.user_id', self._as_id(panel_user.get('username'))),
            ('user_data.user_id', self._as_id(panel_user.get('telegramId'))),
            ('vpn.uuid', panel_user.get('uuid')),
            ('vpn.bypass_uuid', panel_user.get('uuid')),
            ('vpn.shortUuid', panel_user.get('shortUuid')),
            ('vpn.bypass_shortUuid', panel_user.get('shortUuid')),
        ):
            if value:
                found = await self.users.col.find_one({field: value})
                if found:
                    return found
        return None

    @staticmethod
    def _as_id(value) -> int | None:
        """'802421217' и '802421217_bypass' — один и тот же человек."""
        raw = str(value or '').strip().split('_')[0]
        try:
            return int(raw) or None
        except (TypeError, ValueError):
            return None
