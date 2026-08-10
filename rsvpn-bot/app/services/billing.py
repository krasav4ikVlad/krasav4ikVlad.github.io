"""Покупка и продление подписки — сценарий целиком в одном месте.

Хендлер вызывает buy()/extend() и получает результат; он не знает ни про
списание баланса, ни про API панели, ни про подарки.
"""

from __future__ import annotations

import logging

from app.core.errors import (FeatureDisabled, NotEnoughBalance, PlanUnavailable,
                             VpnPanelError)
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)


class BillingService:
    def __init__(self, users, plans, settings, vpn, topup, notifier=None, renewal=None,
                 discounts=None):
        self.users = users
        self.plans = plans
        self.settings = settings
        self.vpn = vpn
        self.topup = topup
        self.notifier = notifier
        # общий сценарий продления: проставляется в Container.attach_bot
        self.renewal = renewal
        self.discounts = discounts

    async def price_for(self, user: dict | None, plan: dict) -> int:
        """Цена тарифа для этого человека — со скидкой его аудитории."""
        if self.discounts is None:
            return int(plan['price'])
        return await self.discounts.price(user, plan)

    async def buy(self, user_id: int, plan_code: str) -> dict:
        if not await self.settings.flag('features.buy_enabled'):
            raise FeatureDisabled

        plan = await self.plans.get(plan_code)
        if not plan or not plan.get('enabled', True):
            raise PlanUnavailable

        user = await self.users.get(user_id)
        balance = self.users.pick(user or {}, 'info.balance', 0)
        # Только цена тарифа: доп. устройства оплачиваются своими пакетами
        # в DeviceBillingService, у них отдельный тридцатидневный цикл.
        # Скидка аудитории — здесь же, чтобы списалось ровно то, что человек
        # видел на кнопке.
        price = await self.price_for(user, plan)

        if balance < price:
            raise NotEnoughBalance(need=price, have=balance)

        # Списываем ДО обращения к панели: charge атомарен и защищает от
        # двойного клика. Если панель не ответит — вернём деньги.
        if not await self.users.charge(user_id, price, f'Покупка подписки «{plan["title"]}»'):
            raise NotEnoughBalance(need=price, have=balance)

        try:
            subscription = await self.vpn.create_subscription(user_id, days=plan['days'])
        except Exception:
            await self.users.credit(user_id, price, 'Возврат: не удалось создать подписку')
            raise

        await self.users.set_vpn(user_id, {
            'period': plan['days'],
            'shortUuid': subscription['shortUuid'],
            'uuid': subscription['uuid'],
            'expireAt': subscription['expireAt'],
            'createdAt': subscription['createdAt'],
        })

        # Обычно ByPass без основной подписки не заводят, но у вернувшихся
        # после удаления подписки он остаётся — и тогда живёт по старой дате.
        from app.services import bypass

        await bypass.sync_expiry(self.users, self.vpn, user_id,
                                 subscription['expireAt'])

        if plan.get('gift_count') and plan.get('gift_type'):
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$inc': {f'info.gifts.{plan["gift_type"]}': plan['gift_count']}},
            )

        if self.notifier:
            await self.notifier.subscription_created(user_id, plan, subscription)

        return {'plan': plan, 'price': price, 'subscription': subscription}

    async def _plan_for(self, vpn: dict) -> dict | None:
        """Тариф продления. Срок без тарифа — берём самый короткий.

        В vpn.period попадала длина бесплатного периода, а тарифа на неё нет.
        Без запасного варианта кнопка «Продлить» отвечала «тариф недоступен»
        всем, кто пришёл через триал и ни разу не выбирал длительность.
        """
        return (await self.plans.by_days(vpn.get('period') or 0)
                or await self.plans.shortest())

    async def extend(self, user_id: int) -> dict:
        """Продление действующей подписки. Новая НЕ создаётся.

        Раньше здесь вызывался buy(), то есть POST /api/users, и панель
        отвечала «User short UUID already exists»: shortUuid считается от
        user_id и у второй подписки совпадает с первой. Продление — это
        сдвиг expireAt у существующей записи, у основной и у ByPass сразу.

        Логика общая с автопродлением (RenewalService.renew): один сценарий —
        одно место, иначе ручное и автоматическое продление разъедутся.
        """
        if not await self.settings.flag('features.extend_enabled'):
            raise FeatureDisabled

        user = await self.users.get(user_id)
        vpn = (user or {}).get('vpn') or {}

        # подписки ещё нет — продлевать нечего, это первая покупка
        if not vpn.get('uuid'):
            plan = await self._plan_for(vpn)
            if not plan:
                raise PlanUnavailable
            return await self.buy(user_id, plan['code'])

        plan = await self._plan_for(vpn)
        if not plan:
            raise PlanUnavailable

        if self.renewal is None:
            raise VpnPanelError('сервис продления не собран')

        expires = parse_dt(vpn.get('expireAt')) or now()
        status = await self.renewal.renew(user, expires)

        price = await self.price_for(user, plan)
        if status == 'no_funds':
            balance = int(self.users.pick(user or {}, 'info.balance', 0) or 0)
            raise NotEnoughBalance(need=price, have=balance)
        if status == 'unknown_plan':
            raise PlanUnavailable
        if status != 'renewed':
            raise VpnPanelError(status)

        return {'plan': plan, 'price': price,
                'subscription': ((await self.users.get(user_id)) or {}).get('vpn', {})}
