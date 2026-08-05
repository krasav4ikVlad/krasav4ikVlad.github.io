"""Подарочные подписки.

Перенос из utils.process_gift_activation / save_gift_to_file. Изменения:

* подарки лежат в Mongo, а не в файле gifts.json. Файл писали два процесса
  (бот и API), и «атомарная» запись через временный файл не спасает от того,
  что второй процесс перечитает и перезапишет весь словарь целиком;
* бесплатный подарок списывается атомарно (`$inc` с проверкой `$gt: 0`).
  Было чтение словаря `info.gifts`, уменьшение в питоне и `$set` целиком —
  два одновременных принятия списывали один и тот же подарок дважды;
* с баланса дарителя тоже списывается атомарно: раньше проверка баланса и
  `$inc` шли отдельно, и даритель мог уйти в минус;
* каталог подарков — тарифы из БД, а не GIFT_PRICES/GIFT_DAYS в config.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)


@dataclass
class GiftResult:
    ok: bool
    reason: str = ''          # used | not_found | self | no_sender | no_funds | panel_error
    days: int = 0
    plan: dict | None = None
    extended: bool = False    # True — продлили существующую подписку
    price: int = 0


class GiftService:
    def __init__(self, users, gifts, plans, settings, vpn, notifier=None):
        self.users = users
        self.gifts = gifts
        self.plans = plans
        self.settings = settings
        self.vpn = vpn
        self.notifier = notifier

    async def ensure_indexes(self) -> None:
        from app.repositories.base import Repository

        await Repository(self.gifts).ensure_index('gift_id', unique=True)

    async def create(self, from_user_id: int, plan_code: str) -> str:
        """Создать подарок и вернуть его id для ссылки."""
        gift_id = uuid4().hex[:16]
        await self.gifts.insert_one({
            'gift_id': gift_id, 'plan_code': plan_code, 'from_user_id': from_user_id,
            'is_accepted': False, 'created_at': now(),
        })
        return gift_id

    async def activate(self, gift_id: str, plan_code: str,
                       from_user_id: int, to_user_id: int) -> GiftResult:
        if not await self.settings.flag('features.gifts_enabled'):
            return GiftResult(False, 'disabled')
        if from_user_id == to_user_id:
            return GiftResult(False, 'self')

        plan = await self._plan(plan_code)
        if not plan:
            return GiftResult(False, 'not_found')

        # Забираем подарок атомарно: второй переход по той же ссылке не пройдёт
        claimed = await self.gifts.update_one(
            {'gift_id': gift_id, 'from_user_id': from_user_id, 'is_accepted': False},
            {'$set': {'is_accepted': True, 'to_user_id': to_user_id, 'accepted_at': now()}},
        )
        if claimed.modified_count != 1:
            return GiftResult(False, 'used')

        sender = await self.users.get(from_user_id, {'info.balance': 1, 'info.gifts': 1})
        if not sender:
            await self._release(gift_id)
            return GiftResult(False, 'no_sender')

        price = int(plan['price'])
        paid_with_free = await self._take_free_gift(from_user_id, plan_code)

        if not paid_with_free and not await self.users.charge(
                from_user_id, price, f'Подарок «{plan["title"]}»'):
            await self._release(gift_id)
            return GiftResult(False, 'no_funds', price=price, plan=plan)

        try:
            extended = await self._grant(to_user_id, plan)
        except VpnPanelError as exc:
            # возвращаем всё как было: подарок снова доступен, деньги на месте
            log.error('подарок %s не выдан: %s', gift_id, exc)
            if paid_with_free:
                await self._return_free_gift(from_user_id, plan_code)
            else:
                await self.users.credit(from_user_id, price, 'Возврат за неудавшийся подарок')
            await self._release(gift_id)
            return GiftResult(False, 'panel_error', plan=plan)

        if self.notifier:
            await self.notifier.gift_accepted(from_user_id, to_user_id, plan=plan)

        return GiftResult(True, days=int(plan['days']), plan=plan,
                          extended=extended, price=0 if paid_with_free else price)

    # ── внутреннее ──────────────────────────────────────────────────────────
    async def _plan(self, code: str) -> dict | None:
        plan = await self.plans.get(code)
        if plan:
            return plan
        aliases = await self.settings.get('gifts.aliases', '')
        for pair in str(aliases).split(','):
            if ':' in pair:
                legacy, actual = (x.strip() for x in pair.split(':', 1))
                if legacy == code:
                    return await self.plans.get(actual)
        return None

    async def _take_free_gift(self, user_id: int, plan_code: str) -> bool:
        """Списать бесплатный подарок, если он есть. Атомарно."""
        result = await self.users.col.update_one(
            {'user_data.user_id': user_id, f'info.gifts.{plan_code}': {'$gt': 0}},
            {'$inc': {f'info.gifts.{plan_code}': -1}},
        )
        return result.modified_count == 1

    async def _return_free_gift(self, user_id: int, plan_code: str) -> None:
        await self.users.col.update_one(
            {'user_data.user_id': user_id}, {'$inc': {f'info.gifts.{plan_code}': 1}})

    async def _release(self, gift_id: str) -> None:
        await self.gifts.update_one(
            {'gift_id': gift_id},
            {'$set': {'is_accepted': False}, '$unset': {'to_user_id': '', 'accepted_at': ''}})

    async def _grant(self, user_id: int, plan: dict) -> bool:
        """Создать подписку или продлить существующую. True — продлили."""
        user = await self.users.get(user_id, {'vpn': 1})
        vpn = (user or {}).get('vpn') or {}
        days = int(plan['days'])

        if not vpn.get('uuid'):
            created = await self.vpn.create_subscription(user_id, days=days)
            await self.users.set_vpn(user_id, {
                'period': days,
                'shortUuid': created.get('shortUuid', ''),
                'uuid': created.get('uuid', ''),
                'expireAt': parse_dt(created.get('expireAt')),
                'createdAt': parse_dt(created.get('createdAt')),
            })
            return False

        current = parse_dt(vpn.get('expireAt'))
        base = current if current and current > now() else now()
        new_expire = base + timedelta(days=days)

        await self.vpn.update_subscription(vpn['uuid'], expire_at=new_expire)
        await self.users.set_vpn(user_id, {'expireAt': new_expire})
        return True
