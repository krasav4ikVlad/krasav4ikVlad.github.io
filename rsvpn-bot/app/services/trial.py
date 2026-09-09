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
    def __init__(self, users, settings, vpn, bot=None, plans=None):
        self.users = users
        self.settings = settings
        self.vpn = vpn
        self.bot = bot
        self.plans = plans

    async def channel(self) -> str:
        return str(await self.settings.get('trial.channel') or '').strip()

    async def available(self, user: dict | None) -> bool:
        """Показывать ли кнопку «получить бесплатно»."""
        if not await self.settings.flag('features.trial_enabled'):
            return False
        if self.claimed(user) or self.blocked_by_subscription(user):
            return False
        return True

    @staticmethod
    def claimed(user: dict | None) -> bool:
        return bool(((user or {}).get('growth') or {}).get('trial_claimed_at'))

    @staticmethod
    def blocked_by_subscription(user: dict | None, moment=None) -> bool:
        """Мешает ли уже существующая подписка выдать бесплатный период.

        Раньше правило было простое: есть shortUuid — триал не положен, и
        навсегда. Тогда сброс триала из админки ничего бы не дал: у любого,
        кто хоть раз покупал, shortUuid остаётся в документе после истечения.

        Правило теперь такое:
        * подписка ДЕЙСТВУЕТ — триал не нужен, человек и так с доступом;
        * подписка была и истекла — только если админ явно сбросил триал.
          Иначе включение сброса раздало бы бесплатные дни всей истёкшей
          базе разом, чего никто не просил.
        """
        from app.core.time import parse_dt

        vpn = (user or {}).get('vpn') or {}
        if not str(vpn.get('shortUuid') or '').strip():
            return False

        expires = parse_dt(vpn.get('expireAt'))
        if expires and expires > (moment or now()):
            return True
        return not ((user or {}).get('growth') or {}).get('trial_reset_at')

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
        if self.blocked_by_subscription(user):
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
            if (user or {}).get('vpn', {}).get('uuid'):
                fields = await self._extend_existing(user, days)
            else:
                fields = await self._create_new(user_id, days)
        except Exception as exc:
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$unset': {'growth.trial_claimed_at': ''}})
            log.error('бесплатный период %s не выдан: %s', user_id, exc)
            return TrialResult(False, 'panel')

        await self.users.set_vpn(user_id, fields)

        log.info('%s получил бесплатный период на %s дней', user_id, days)
        return TrialResult(True, days=days)

    async def _create_new(self, user_id: int, days: int) -> dict:
        subscription = await self.vpn.create_subscription(user_id, days=days)
        return {
            # vpn.period — это НЕ длина бесплатного периода, а тариф, по
            # которому потом продлевать. Здесь стояло `days` (3 дня), тарифа
            # на 3 дня нет, и автопродление после триала молча не срабатывало:
            # by_days(3) не находит план и списание не происходит вообще.
            'period': await self._default_period(days),
            'shortUuid': subscription['shortUuid'],
            'uuid': subscription['uuid'],
            'expireAt': subscription['expireAt'],
            'createdAt': subscription['createdAt'],
        }

    async def _default_period(self, days: int) -> int:
        """Тариф для продления после бесплатного периода — самый короткий."""
        if not self.plans:
            return 1
        plan = await self.plans.by_days(days) or await self.plans.shortest()
        return int((plan or {}).get('days') or 1)

    async def _extend_existing(self, user: dict, days: int) -> dict:
        """Повторный триал после сброса: подписка в панели уже есть.

        Создавать вторую нельзя — панель ответит «User short UUID already
        exists»: shortUuid считается от user_id и совпадёт. Поэтому сдвигаем
        дату у существующей и включаем её, если была отключена.
        """
        from datetime import timedelta

        from app.core.time import parse_dt

        vpn = user.get('vpn') or {}
        moment = now()
        expires = parse_dt(vpn.get('expireAt'))
        base = expires if expires and expires > moment else moment
        new_expire = base + timedelta(days=days)

        await self.vpn.update_subscription(vpn['uuid'], expire_at=new_expire,
                                           status='ACTIVE')

        # ByPass живёт на той же дате — выравниваем общим способом, чтобы
        # правило было одно на все продления, а не своё в каждом сервисе.
        from app.services import bypass

        fields = {'expireAt': new_expire}
        await bypass.sync_expiry(self.users, self.vpn,
                                 self.users.pick(user, 'user_data.user_id'),
                                 new_expire, vpn=vpn, status='ACTIVE')

        # Длительность не трогаем, если человек её уже выбирал: иначе
        # автопродление пошло бы искать тариф на 3 дня и не нашло.
        if not int(vpn.get('period') or 0):
            fields['period'] = days
        return fields

    # ── сброс из админки ────────────────────────────────────────────────────
    async def reset(self, query: dict | None = None) -> int:
        """Разрешить брать бесплатный период заново. Возвращает, скольким.

        Метка claimed снимается, а вместо неё ставится trial_reset_at — по
        ней blocked_by_subscription() понимает, что истёкшей подписке в этот
        раз можно не мешать.
        """
        result = await self.users.col.update_many(
            {**(query or {}), 'growth.trial_claimed_at': {'$exists': True}},
            {'$unset': {'growth.trial_claimed_at': ''},
             '$set': {'growth.trial_reset_at': now()}},
        )
        return int(getattr(result, 'modified_count', 0) or 0)

    async def claimed_count(self, query: dict | None = None) -> int:
        return await self.users.col.count_documents(
            {**(query or {}), 'growth.trial_claimed_at': {'$exists': True}})
