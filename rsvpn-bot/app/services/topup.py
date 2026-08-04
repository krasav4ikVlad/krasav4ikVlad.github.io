"""Единственный путь, которым деньги попадают на баланс.

Идемпотентность, бонусы, рефералка, уведомления, аналитика — всё здесь и
только здесь. Любой платёжный провайдер и ручное начисление из админки
проходят через process().
"""

from __future__ import annotations

import logging

from app.domain.pricing import PricingRules, referral_reward, topup_credit

log = logging.getLogger(__name__)


class TopupService:
    def __init__(self, users, payments, settings, notifier=None, analytics=None):
        self.users = users
        self.payments = payments
        self.settings = settings
        self.notifier = notifier
        self.analytics = analytics

    async def process(self, *, provider: str, txid: str, amount: int,
                      user_id: int | None, payload: dict) -> dict:
        if amount <= 0:
            return {'status': 'bad_amount'}
        if user_id is None:
            return {'status': 'unknown_user'}

        # 1. Идемпотентность: повторный вебхук не начисляет второй раз
        if not await self.payments.register(txid, user_id, amount, provider, payload):
            log.info('[%s] txid=%s уже обработан', provider, txid)
            return {'status': 'duplicate', 'txid': txid}

        # 2. Бонусы по текущим настройкам
        rules = await self.rules()
        extra = (await self.settings.rate('bonus.tribute_extra_rate')
                 if provider == 'tribute' else 0.0)
        credit, bonus = topup_credit(amount, rules, extra_rate=extra)

        # 3. Начисление
        if not await self.users.credit(user_id, credit,
                                       f'Пополнение ({provider}), бонус {bonus}₽'):
            await self.payments.mark(txid, 'user_not_found')
            return {'status': 'user_not_found', 'user_id': user_id}

        await self.payments.mark(txid, 'done', credited=credit, bonus=bonus)

        # 4. Реферальное вознаграждение — от базовой суммы, не от бонуса
        await self._pay_referrer(user_id, amount, rules)

        if self.notifier:
            await self.notifier.topup(user_id, amount=amount, bonus=bonus,
                                      credit=credit, provider=provider)
        if self.analytics:
            await self.analytics.track('payment_success', user_id, {
                'amount': amount, 'provider': provider, 'credit': credit, 'txid': txid,
            })

        return {'status': 'ok', 'user_id': user_id, 'credit': credit, 'bonus': bonus}

    async def rules(self) -> PricingRules:
        """Снимок настроек для расчётов."""
        bonus_on = await self.settings.flag('bonus.topup_enabled')
        return PricingRules(
            device_price=await self.settings.int('price.device_extra'),
            free_devices=await self.settings.int('price.devices_free_limit'),
            topup_bonus_rate=await self.settings.rate('bonus.topup_rate') if bonus_on else 0.0,
            referral_rate=await self.settings.rate('bonus.ref_rate'),
            sleeping_discount=await self.settings.rate('bonus.sleeping_discount'),
            gateway_fee_rate=await self.settings.rate('pay.fee_rate'),
        )

    async def _pay_referrer(self, user_id: int, amount: int, rules: PricingRules) -> None:
        if not await self.settings.flag('features.referrals_enabled'):
            return

        user = await self.users.get(user_id, {'user_data.referrer': 1})
        referrer_id = self.users.pick(user or {}, 'user_data.referrer')
        if not referrer_id:
            return

        reward = referral_reward(amount, rules)
        if reward <= 0:
            return

        await self.users.col.update_one(
            {'user_data.user_id': referrer_id},
            {
                '$inc': {
                    'info.ref_stats.withdrawable': reward,
                    'info.ref_stats.earned_total': reward,
                    'info.ref_stats.turnover_total': amount,
                    'info.ref_stats.payments_count': 1,
                },
                '$addToSet': {'info.ref_stats.paying_referrals': user_id},
            },
        )
        await self.users.log(referrer_id, 'Реферальное начисление', f'+{reward}₽')
