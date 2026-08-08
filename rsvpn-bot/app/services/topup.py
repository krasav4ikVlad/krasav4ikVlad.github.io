"""Единственный путь, которым деньги попадают на баланс.

Сюда сходятся все шесть платёжных систем и ручное начисление из админки.
Порядок шагов важен и он один на всех: идемпотентность → бонусы → зачисление
→ рефералка → уведомления. В старом FastApi.process_topup этот порядок был
описан один раз, но проверка дубля стояла ПОСЛЕ расчёта бонуса, а часть
провайдеров вообще шла мимо (cards_ru считал бонус сам и вызывал process_topup
с уже увеличенной суммой — то есть бонус начислялся дважды).
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.domain.pricing import PricingRules, referral_reward, topup_credit

log = logging.getLogger(__name__)

EXPIRED_SEGMENTS = (
    'expired_1d', 'expired_3d', 'expired_7d', 'expired_14d', 'expired_21d',
    'expired_30d', 'churned_45d', 'churned_60d', 'churned_90d', 'churned_dead',
)
AB_BONUS_GROUPS = ('bonus_30', 'bonus_15')


class TopupService:
    def __init__(self, users, payments, settings, container=None,
                 notifier=None, analytics=None):
        self.users = users
        self.payments = payments
        self.settings = settings
        self.container = container
        self.notifier = notifier
        self.analytics = analytics

    async def process(self, *, provider: str, txid: str, amount: int,
                      user_id: int | None, payload: dict | None = None) -> dict:
        if amount <= 0:
            return {'status': 'bad_amount'}
        if not user_id:
            return {'status': 'unknown_user'}
        if not txid:
            return {'status': 'no_txid'}

        # 1. Идемпотентность — прежде любых расчётов и начислений
        if not await self.payments.register(txid, user_id, amount, provider, payload or {}):
            log.info('[%s] txid=%s уже обработан', provider, txid)
            return {'status': 'duplicate', 'txid': txid, 'user_id': user_id}

        user = await self.users.get(user_id)
        if not user:
            await self.payments.mark(txid, 'user_not_found')
            log.error('[%s] пользователь %s не найден', provider, user_id)
            return {'status': 'user_not_found', 'user_id': user_id}

        # 2. Бонусы
        rules = await self.rules()
        extra_rate = 0.0
        if provider == 'tribute':
            extra_rate += await self.settings.rate('bonus.tribute_extra_rate')

        personal = float(self.users.pick(user, 'info.bonus_multiplier', 0) or 0)
        if personal > 0:
            extra_rate += personal

        credit, bonus = topup_credit(amount, rules, extra_rate=extra_rate)
        ab_bonus, ab_group = await self._ab_bonus(user, amount)
        credit += ab_bonus

        # 3. Зачисление
        description = f'Пополнение ({provider}), бонус {bonus + ab_bonus}₽'
        if not await self.users.credit(user_id, credit, description):
            await self.payments.mark(txid, 'credit_failed')
            return {'status': 'credit_failed', 'user_id': user_id}

        if personal > 0:
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$set': {'info.bonus_multiplier': 0.0}})

        await self.payments.mark(txid, 'done', credited=credit,
                                 bonus=bonus + ab_bonus, ab_group=ab_group)
        await self.users.log(user_id, 'Пополнение баланса',
                             f'+{credit}₽ ({provider}), база {amount}₽')

        # 4. Пометки для аналитики воронок
        await self._mark_campaign_conversion(user, amount)

        # 5. Реферальное вознаграждение — от базовой суммы, не от бонусов
        reward, referrer_id = await self._pay_referrer(user, amount, rules)

        if self.notifier:
            await self.notifier.topup(user_id, amount=amount, bonus=bonus + ab_bonus,
                                      credit=credit, provider=provider)
        if self.analytics:
            await self.analytics.track('payment_success', user_id, {
                'amount': amount, 'provider': provider, 'credit': credit,
                'bonus': bonus, 'ab_bonus': ab_bonus, 'txid': txid,
            })

        log.info('[%s] %s: +%s₽ (база %s, бонус %s, A/B %s)',
                 provider, user_id, credit, amount, bonus, ab_bonus)

        return {'status': 'ok', 'user_id': user_id, 'amount': amount,
                'bonus': bonus, 'ab_bonus': ab_bonus, 'credit': credit,
                'referral_reward': reward, 'referrer_id': referrer_id}

    # ── настройки расчётов ──────────────────────────────────────────────────
    async def rules(self) -> PricingRules:
        bonus_on = await self.settings.flag('bonus.topup_enabled')
        return PricingRules(
            device_price=await self.settings.int('price.device_extra'),
            free_devices=await self.settings.int('price.devices_free_limit'),
            topup_bonus_rate=await self.settings.rate('bonus.topup_rate') if bonus_on else 0.0,
            referral_rate=await self.settings.rate('bonus.ref_rate'),
            gateway_fee_rate=await self.settings.rate('pay.fee_rate'),
        )

    # ── A/B бонус новичкам ──────────────────────────────────────────────────
    async def _ab_bonus(self, user: dict, amount: int) -> tuple[int, str | None]:
        group = self.users.pick(user, 'growth.ab_group')
        segment = str(self.users.pick(user, 'growth.segment', '') or '')

        if group not in AB_BONUS_GROUPS or not segment.startswith('new_trial'):
            return 0, None

        bonus = int(amount * await self.settings.rate('bonus.ab_new_trial_rate'))
        if bonus <= 0:
            return 0, None

        # 'used' гасит группу: бонус даётся один раз, как и было задумано
        await self.users.col.update_one(
            {'user_data.user_id': self.users.pick(user, 'user_data.user_id')},
            {'$set': {
                'growth.ab_group': 'used',
                'campaigns.converted_from': group,
                'campaigns.converted_at': now(),
                'campaigns.converted_amount': amount,
            }},
        )
        return bonus, group

    # ── конверсия из кампаний ───────────────────────────────────────────────
    async def _mark_campaign_conversion(self, user: dict, amount: int) -> None:
        campaigns = user.get('campaigns') or {}
        if campaigns.get('expired_converted_from'):
            return

        segment = self.users.pick(user, 'growth.segment', '')
        touched = next(
            (seg for seg in EXPIRED_SEGMENTS
             if seg == segment or any(k.startswith(f'{seg}_s') or k == f'{seg}_sent'
                                      for k in campaigns)),
            None,
        )
        if not touched:
            return

        await self.users.col.update_one(
            {'user_data.user_id': self.users.pick(user, 'user_data.user_id')},
            {'$set': {
                'campaigns.expired_converted_from': touched,
                'campaigns.expired_converted_at': now(),
                'campaigns.expired_converted_amount': amount,
            }},
        )

    # ── рефералка ───────────────────────────────────────────────────────────
    async def _pay_referrer(self, user: dict, amount: int,
                            rules: PricingRules) -> tuple[int, int | None]:
        if not await self.settings.flag('features.referrals_enabled'):
            return 0, None

        referrer_id = self.users.pick(user, 'user_data.referrer')
        friend_id = self.users.pick(user, 'user_data.user_id')
        if not referrer_id or not friend_id:
            return 0, None

        reward = referral_reward(amount, rules)
        if reward <= 0:
            return 0, referrer_id

        result = await self.users.col.update_one(
            {'user_data.user_id': referrer_id},
            {
                '$inc': {
                    'info.ref_stats.withdrawable': reward,
                    'info.ref_stats.earned_total': reward,
                    'info.ref_stats.turnover_total': amount,
                    'info.ref_stats.payments_count': 1,
                },
                '$addToSet': {
                    'info.ref_stats.paying_referrals': friend_id,
                    'info.ref_stats.referrals': friend_id,
                },
                '$push': {'info.transactions': {
                    'amount': reward, 'dt': now(), 'description': 'Реферальное начисление',
                    'meta': {'friend_id': friend_id, 'friend_topup': amount},
                }},
            },
        )
        if result.matched_count == 0:
            return 0, referrer_id

        await self.users.log(referrer_id, 'Реферальное начисление',
                             f'+{reward}₽ от друга {friend_id}')
        return reward, referrer_id
