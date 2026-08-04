"""Покупка и продление подписки — сценарий целиком в одном месте.

Хендлер вызывает buy()/extend() и получает результат; он не знает ни про
списание баланса, ни про API панели, ни про подарки.
"""

from __future__ import annotations

import logging

from app.core.errors import FeatureDisabled, NotEnoughBalance, PlanUnavailable
from app.domain.pricing import subscription_price

log = logging.getLogger(__name__)


class BillingService:
    def __init__(self, users, plans, settings, vpn, topup, notifier=None):
        self.users = users
        self.plans = plans
        self.settings = settings
        self.vpn = vpn
        self.topup = topup
        self.notifier = notifier

    async def buy(self, user_id: int, plan_code: str) -> dict:
        if not await self.settings.flag('features.buy_enabled'):
            raise FeatureDisabled

        plan = await self.plans.get(plan_code)
        if not plan or not plan.get('enabled', True):
            raise PlanUnavailable

        user = await self.users.get(user_id)
        balance = self.users.pick(user or {}, 'info.balance', 0)
        rules = await self.topup.rules()
        price = subscription_price(plan['price'],
                                   self.users.pick(user or {}, 'vpn.hwidDeviceLimit', 0),
                                   rules)

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

        if plan.get('gift_count') and plan.get('gift_type'):
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$inc': {f'info.gifts.{plan["gift_type"]}': plan['gift_count']}},
            )

        if self.notifier:
            await self.notifier.subscription_created(user_id, plan, subscription)

        return {'plan': plan, 'price': price, 'subscription': subscription}

    async def extend(self, user_id: int) -> dict:
        """Продление. Тумблер features.extend_enabled выключает его целиком."""
        if not await self.settings.flag('features.extend_enabled'):
            raise FeatureDisabled

        user = await self.users.get(user_id)
        plan = await self.plans.by_days(self.users.pick(user or {}, 'vpn.period', 0))
        if not plan:
            raise PlanUnavailable

        return await self.buy(user_id, plan['code'])
