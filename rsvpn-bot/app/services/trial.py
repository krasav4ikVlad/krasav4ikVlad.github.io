"""Бесплатный период за подписку на канал.

Заменяет стартовый баланс: раньше каждому новому пользователю начислялись
деньги просто за нажатие /start — их получали и те, кто зашёл случайно, и
те, кто перезаходил с новых аккаунтов. Теперь бесплатные дни выдаются один
раз и только тому, кто подписался на канал.

Проверка подписки идёт через getChatMember у самого Telegram: доверять
кнопке «я подписался» нельзя, отписаться можно через секунду после нажатия.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.time import now

log = logging.getLogger(__name__)

# Статусы, при которых человек считается подписанным. 'restricted' — тоже
# подписан, если is_member: это ограниченный участник, а не покинувший канал.
MEMBER_STATUSES = {'member', 'administrator', 'creator'}


@dataclass
class TrialResult:
    ok: bool
    reason: str = ''       # disabled | claimed | has_sub | not_subscribed | panel | check
    days: int = 0


class TrialService:
    def __init__(self, users, settings, vpn, bot=None):
        self.users = users
        self.settings = settings
        self.vpn = vpn
        self.bot = bot

    async def channel(self) -> str:
        return str(await self.settings.get('trial.channel') or '').strip()

    async def available(self, user: dict | None) -> bool:
        """Показывать ли кнопку «получить бесплатно»."""
        if not await self.settings.flag('features.trial_enabled'):
            return False
        if self.claimed(user) or (user or {}).get('vpn', {}).get('shortUuid'):
            return False
        return True

    @staticmethod
    def claimed(user: dict | None) -> bool:
        return bool(((user or {}).get('growth') or {}).get('trial_claimed_at'))

    async def is_subscribed(self, user_id: int) -> bool | None:
        """True/False — ответ Telegram. None — проверить не удалось.

        Разница важна: «не подписан» и «бот не может проверить» — разные
        случаи. Во втором виноваты мы (бота не добавили в канал админом),
        и наказывать за это человека отказом нельзя.
        """
        channel = await self.channel()
        if not channel or self.bot is None:
            return None

        try:
            member = await self.bot.get_chat_member(chat_id=channel, user_id=user_id)
        except Exception as exc:
            log.warning('подписка на %s не проверена: %s', channel, exc)
            return None

        status = getattr(member, 'status', '')
        if status in MEMBER_STATUSES:
            return True
        if status == 'restricted':
            return bool(getattr(member, 'is_member', False))
        return False

    async def claim(self, user_id: int) -> TrialResult:
        """Выдать бесплатный период. Повторно не выдаётся никогда."""
        if not await self.settings.flag('features.trial_enabled'):
            return TrialResult(False, 'disabled')

        user = await self.users.get(user_id)
        if self.claimed(user):
            return TrialResult(False, 'claimed')
        if (user or {}).get('vpn', {}).get('shortUuid'):
            return TrialResult(False, 'has_sub')

        if await self.settings.flag('trial.require_subscription'):
            subscribed = await self.is_subscribed(user_id)
            if subscribed is None:
                return TrialResult(False, 'check')
            if not subscribed:
                return TrialResult(False, 'not_subscribed')

        days = await self.settings.int('price.trial_days')

        # Метку ставим ДО обращения к панели и атомарно: двойное нажатие не
        # должно создать две подписки. Не получилось — метку снимаем.
        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             'growth.trial_claimed_at': {'$exists': False}},
            {'$set': {'growth.trial_claimed_at': now()}},
        )
        if claimed.modified_count != 1:
            return TrialResult(False, 'claimed')

        try:
            subscription = await self.vpn.create_subscription(user_id, days=days)
        except Exception as exc:
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$unset': {'growth.trial_claimed_at': ''}})
            log.error('бесплатный период %s не выдан: %s', user_id, exc)
            return TrialResult(False, 'panel')

        await self.users.set_vpn(user_id, {
            'period': days,
            'shortUuid': subscription['shortUuid'],
            'uuid': subscription['uuid'],
            'expireAt': subscription['expireAt'],
            'createdAt': subscription['createdAt'],
        })

        log.info('%s получил бесплатный период на %s дней', user_id, days)
        return TrialResult(True, days=days)
