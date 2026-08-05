"""Уведомления об истечении подписки по вебхукам Remnawave.

Панель сама знает, когда подписка истекает, и присылает событие. Значит боту
не нужно раз в 15 минут перебирать всех пользователей ради напоминаний — это
делал process_subscriptions (≈290 строк, из них половина про напоминания).

События (docs.rw/features/webhooks):
    user.expiration  — meta.expiration: часы со знаком.
                       −72 = «истечёт через 72 часа», +24 = «истекла 24 часа назад».
                       Работает при EXPIRATION_NOTIFICATIONS_ENABLED=true в панели,
                       пороги задаются там же в EXPIRATION_NOTIFICATIONS.
    user.expired     — подписка закончилась.
    Плюс старые имена (user.expires_in_24_hours и т.п.), убранные в 2.8.0, —
    поддержаны, чтобы работало и до обновления панели.

Ключ напоминания пишется в vpn.notified.<key> тем же атомарным приёмом, что и
слоты кампаний: повторная доставка вебхука (панель ретраит) не даст второго
сообщения. При продлении флаги сбрасываются — иначе в следующем цикле человек
не получит ни одного напоминания.
"""

from __future__ import annotations

import logging

from app.core.time import now, parse_dt
from app.content import texts

log = logging.getLogger(__name__)

EXPIRATION_EVENT = 'user.expiration'
EXPIRED_EVENT = 'user.expired'

# Пороги «осталось часов» → ключ напоминания.
REMINDERS: tuple[tuple[int, str], ...] = (
    (72, '3d'), (48, '2d'), (24, '1d'),
    (12, '12h'), (6, '6h'), (3, '3h'), (1, '1h'),
)
# Пороги «прошло часов после истечения» → ключ.
AFTER_EXPIRY: tuple[tuple[int, str], ...] = (
    (72, 'expired_72h'), (24, 'expired_24h'), (0, 'expired'),
)

# Старые имена событий (панель до 2.8.0) → часы до истечения.
LEGACY_EVENTS = {
    'user.expires_in_72_hours': -72,
    'user.expires_in_48_hours': -48,
    'user.expires_in_24_hours': -24,
    'user.expired_24_hours_ago': 24,
}

ALL_KEYS = tuple(key for _, key in REMINDERS) + tuple(key for _, key in AFTER_EXPIRY)


def key_for_hours(hours: int) -> str | None:
    """meta.expiration → ключ напоминания.

    Отрицательное значение — сколько осталось, положительное — сколько прошло.
    Берётся ближайший порог, чтобы бот не молчал, если в панели выставили
    свои значения (например 36 часов).
    """
    if hours < 0:
        left = abs(hours)
        for threshold, key in REMINDERS:
            if left >= threshold:
                return key
        return REMINDERS[-1][1]

    for threshold, key in AFTER_EXPIRY:
        if hours >= threshold:
            return key
    return 'expired'


class ExpiryNotifier:
    def __init__(self, users, settings, sender, bot, keyboards=None):
        self.users = users
        self.settings = settings
        self.sender = sender
        self.bot = bot
        self.keyboards = keyboards or {}

    async def handle(self, event: str, data: dict, meta: dict) -> dict:
        if not await self.settings.flag('expiry.notify_enabled'):
            return {'ok': True, 'note': 'disabled'}

        hours = self._hours(event, meta)
        if hours is None:
            return {'ok': True, 'note': f'ignored_{event}'}

        user = await self._find_user(data)
        if not user:
            return {'ok': True, 'note': 'user_not_found'}

        # Событие про ByPass-подписку — это отдельный пользователь в панели.
        # Ищем его отдельным запросом (а не «не нашли по vpn.uuid, значит чужой»),
        # чтобы в логе было видно осознанный пропуск, а не потерянный юзер.
        uuid = data.get('uuid') or data.get('id')
        if uuid and uuid == self.users.pick(user, 'vpn.bypass_uuid'):
            return {'ok': True, 'note': 'bypass_ignored'}

        key = key_for_hours(hours)
        if not key or not await self.settings.flag(f'expiry.send_{key}'):
            return {'ok': True, 'note': f'skipped_{key}'}

        user_id = self.users.pick(user, 'user_data.user_id')
        if not user_id:
            return {'ok': True, 'note': 'no_user_id'}

        # атомарная пометка: панель ретраит вебхуки, второе сообщение не уйдёт
        claimed = await self.users.col.update_one(
            {'_id': user['_id'], f'vpn.notified.{key}': {'$ne': True}},
            {'$set': {f'vpn.notified.{key}': True, f'vpn.notified_at.{key}': now()}},
        )
        if claimed.modified_count != 1:
            return {'ok': True, 'note': f'already_sent_{key}'}

        text = texts.render(
            f'expiry.{key}',
            name=self.users.pick(user, 'user_data.first_name'),
            balance=self.users.pick(user, 'info.balance', 0),
            expire=self._expire_text(user),
        )
        markup = self.keyboards.get('extend')
        sent = await self.sender.send(self.bot, user_id, text, markup() if markup else None)

        log.info('напоминание %s пользователю %s: %s', key, user_id, 'ок' if sent else 'не дошло')
        return {'ok': True, 'note': f'sent_{key}', 'user_id': user_id, 'delivered': sent}

    async def reset_after_renewal(self, user_id: int) -> None:
        """Сбросить флаги напоминаний — вызывается после продления."""
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$unset': {'vpn.notified': '', 'vpn.notified_at': ''}},
        )

    # ── внутреннее ──────────────────────────────────────────────────────────
    @staticmethod
    def _hours(event: str, meta: dict) -> int | None:
        if event in LEGACY_EVENTS:
            return LEGACY_EVENTS[event]
        if event == EXPIRED_EVENT:
            return 0
        if event != EXPIRATION_EVENT:
            return None
        try:
            return int(meta.get('expiration'))
        except (TypeError, ValueError):
            return None

    async def _find_user(self, data: dict) -> dict | None:
        """Панель может прислать uuid, shortUuid или telegramId — ищем по всем."""
        uuid = data.get('uuid') or data.get('id')
        for field, value in (
            ('vpn.uuid', uuid),
            ('vpn.bypass_uuid', uuid),
            ('vpn.shortUuid', data.get('shortUuid')),
            ('vpn.bypass_shortUuid', data.get('shortUuid')),
            ('user_data.user_id', self._as_int(data.get('telegramId'))),
        ):
            if value:
                user = await self.users.col.find_one({field: value})
                if user:
                    return user
        return None

    @staticmethod
    def _as_int(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _expire_text(self, user: dict) -> str:
        expire = parse_dt(self.users.pick(user, 'vpn.expireAt'))
        return expire.strftime('%d.%m.%Y %H:%M') if expire else '—'
