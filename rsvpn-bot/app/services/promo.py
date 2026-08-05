"""Активация промокодов.

Перенос из utils.redeem_promo_for_user. Логика защиты от повторной активации
там была правильная (уникальный индекс на promo_usages + откат счётчика), но
откат был выписан четыре раза подряд, а тип награды разбирался в if/elif —
добавить третий тип значило снова скопировать всю обвязку.

Здесь: награда — это функция в REWARDS, откат — один метод. Новый тип награды
добавляется одной функцией, всё остальное общее.
"""

from __future__ import annotations

import logging
import re

from app.core.time import now

log = logging.getLogger(__name__)

GB = 1024 ** 3


def normalize_code(code: str) -> str:
    return re.sub(r'[^A-Z0-9_-]', '', (code or '').strip().upper())


class PromoResult:
    def __init__(self, ok: bool, found: bool, reason: str = '', reward: str = '', promo=None):
        self.ok = ok
        self.found = found
        self.reason = reason      # not_found | used | limit | no_bypass | panel_error | bad_type
        self.reward = reward      # человекочитаемая награда: «+100₽»
        self.promo = promo

    def __repr__(self) -> str:
        return f'<PromoResult ok={self.ok} reason={self.reason!r}>'


class PromoService:
    def __init__(self, users, codes, usages, settings, vpn=None):
        self.users = users
        self.codes = codes
        self.usages = usages
        self.settings = settings
        self.vpn = vpn

    async def ensure_indexes(self) -> None:
        from app.repositories.base import Repository

        await Repository(self.codes).ensure_index('code', unique=True)
        # именно этот индекс делает повторную активацию невозможной
        await Repository(self.usages).ensure_index(
            [('promo_id', 1), ('user_id', 1)], unique=True)

    async def redeem(self, user_id: int, raw_code: str) -> PromoResult:
        if not await self.settings.flag('features.promo_enabled'):
            return PromoResult(False, False, 'disabled')

        code = normalize_code(raw_code)
        if len(code) < 3:
            return PromoResult(False, False, 'not_found')

        promo = await self.codes.find_one({
            'code': code, 'is_active': True,
            '$or': [{'expires_at': None}, {'expires_at': {'$gt': now()}}],
        })
        if not promo:
            return PromoResult(False, False, 'not_found')

        # 1. Заявка на активацию: уникальный индекс отсекает второй заход
        try:
            await self.usages.insert_one({
                'promo_id': promo['_id'], 'user_id': user_id, 'code': promo['code'],
                'reward_type': promo.get('reward_type'), 'reward_value': promo.get('reward_value'),
                'created_at': now(),
            })
        except Exception:
            return PromoResult(False, True, 'used', promo=promo)

        # 2. Лимит активаций — проверяется тем же запросом, что и увеличивается
        limit_filter = {'_id': promo['_id'], 'is_active': True}
        if int(promo.get('max_uses', 0) or 0) > 0:
            limit_filter['used_count'] = {'$lt': promo['max_uses']}

        updated = await self.codes.find_one_and_update(
            limit_filter, {'$inc': {'used_count': 1}}, return_document=True)
        if not updated:
            await self._rollback(promo, user_id, count=False)
            return PromoResult(False, True, 'limit', promo=promo)

        # 3. Выдача награды
        reward_type = promo.get('reward_type')
        handler = REWARDS.get(reward_type)
        if not handler:
            await self._rollback(promo, user_id)
            return PromoResult(False, True, 'bad_type', promo=promo)

        ok, reward_text, reason = await handler(self, user_id, int(promo['reward_value']), promo)
        if not ok:
            await self._rollback(promo, user_id)
            return PromoResult(False, True, reason, promo=promo)

        log.info('промокод %s активирован пользователем %s: %s', code, user_id, reward_text)
        return PromoResult(True, True, reward=reward_text, promo=promo)

    async def _rollback(self, promo: dict, user_id: int, count: bool = True) -> None:
        """Отменить заявку и, если счётчик уже увеличен, вернуть его назад."""
        await self.usages.delete_one({'promo_id': promo['_id'], 'user_id': user_id})
        if count:
            await self.codes.update_one(
                {'_id': promo['_id'], 'used_count': {'$gt': 0}},
                {'$inc': {'used_count': -1}})


# ── типы наград ─────────────────────────────────────────────────────────────
async def _reward_balance(service: PromoService, user_id: int, value: int, promo: dict):
    credited = await service.users.credit(user_id, value, f'Промокод {promo["code"]}')
    return (True, f'+{value}₽', '') if credited else (False, '', 'user_not_found')


async def _reward_traffic(service: PromoService, user_id: int, value: int, promo: dict):
    user = await service.users.get(user_id, {'vpn.bypass_uuid': 1, 'vpn.bypass_trafficLimitBytes': 1})
    bypass_uuid = service.users.pick(user or {}, 'vpn.bypass_uuid')
    if not bypass_uuid:
        return False, '', 'no_bypass'

    current = int(service.users.pick(user or {}, 'vpn.bypass_trafficLimitBytes', 0) or 0)
    try:
        await service.vpn.update_subscription(bypass_uuid, traffic_bytes=current + value * GB)
    except Exception as exc:
        log.warning('промокод на трафик не применён для %s: %s', user_id, exc)
        return False, '', 'panel_error'

    await service.users.col.update_one(
        {'user_data.user_id': user_id},
        {'$inc': {'vpn.bypass_trafficLimitBytes': value * GB},
         '$push': {'info.bypass_stats.purchases': {
             'amount_gb': value, 'price': 0, 'promo_code': promo['code'],
             'created_at': now(), 'type': 'promo'}}},
    )
    return True, f'+{value} Гб', ''


REWARDS = {
    'balance': _reward_balance,
    'traffic_gb': _reward_traffic,
}
