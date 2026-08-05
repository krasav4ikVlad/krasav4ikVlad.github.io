"""Заявки на вывод реферального баланса.

Перенос из handlers/payout_system.py. Сама логика там аккуратная — атомарная
метка заявки, откат при ошибке отправки, история действий. Что изменено:

* суммы и кулдаун — настройки, а не константы MIN_WITHDRAW / PAYOUT_COOLDOWN_HOURS;
* чат и тема заявок — из настроек уведомлений;
* решения админа (на баланс / выведено вручную / отказ) собраны в один сервис,
  а рендер карточки и кнопки остались в слое бота — раньше всё это лежало
  вперемешку в одном файле.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.time import hours_since, now

log = logging.getLogger(__name__)

REJECT_REASONS = {
    'data': 'неправильно указаны реквизиты (номер карты/телефона или банк).',
    'form': 'данные заполнены некорректно (например, ФИО или телефон в неверном формате).',
    'min': 'сумма вывода меньше минимальной для выбранного способа.',
    'other': 'заявка не может быть обработана. Свяжитесь с поддержкой.',
}


@dataclass
class PayoutRequest:
    ok: bool
    reason: str = ''          # disabled | below_min | pending | cooldown
    amount: int = 0
    wait_hours: float = 0.0
    method: str = ''


class PayoutService:
    def __init__(self, users, settings, notifier=None):
        self.users = users
        self.settings = settings
        self.notifier = notifier

    async def request(self, user_id: int) -> PayoutRequest:
        if not await self.settings.flag('features.payouts_enabled'):
            return PayoutRequest(False, 'disabled')

        user = await self.users.get(user_id, {'info.ref_stats': 1})
        stats = self.users.pick(user or {}, 'info.ref_stats', {}) or {}
        amount = int(stats.get('withdrawable', 0) or 0)

        minimum = await self.settings.int('payout.min_withdraw')
        if amount < minimum:
            return PayoutRequest(False, 'below_min', amount=amount)

        if stats.get('pending_payout_active'):
            return PayoutRequest(False, 'pending', amount=amount)

        cooldown = await self.settings.int('payout.cooldown_hours')
        last = stats.get('last_payout_request_at')
        if last and cooldown:
            passed = hours_since(last)
            if passed < cooldown:
                return PayoutRequest(False, 'cooldown', amount=amount,
                                     wait_hours=round(cooldown - passed, 1))

        # Метку ставим атомарно: двойной клик не создаст две заявки
        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             'info.ref_stats.pending_payout_active': {'$ne': True}},
            {'$set': {'info.ref_stats.pending_payout_active': True,
                      'info.ref_stats.last_payout_request_at': now()}},
        )
        if claimed.modified_count != 1:
            return PayoutRequest(False, 'pending', amount=amount)

        return PayoutRequest(True, amount=amount,
                             method=stats.get('payout_selected', 'bot_balance'))

    async def cancel_request(self, user_id: int) -> None:
        """Снять метку — например, если заявку не удалось отправить админам."""
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.pending_payout_active': False}})

    # ── решения администратора ──────────────────────────────────────────────
    async def to_balance(self, user_id: int, admin_id: int) -> int:
        """Перевести реферальный баланс на обычный. Возвращает сумму."""
        user = await self.users.get(user_id, {'info.ref_stats.withdrawable': 1})
        amount = int(self.users.pick(user or {}, 'info.ref_stats.withdrawable', 0) or 0)
        if amount <= 0:
            await self._finish(user_id, 'to_balance', 0, admin_id)
            return 0

        moved = await self.users.col.update_one(
            {'user_data.user_id': user_id, 'info.ref_stats.withdrawable': amount},
            {'$inc': {'info.balance': amount},
             '$set': {'info.ref_stats.withdrawable': 0},
             '$push': {'info.transactions': {
                 'amount': amount, 'dt': now(), 'description': 'Перевод реферального баланса'}}},
        )
        if moved.modified_count != 1:
            return -1      # баланс изменился между показом и нажатием

        await self._finish(user_id, 'to_balance', amount, admin_id)
        return amount

    async def paid_externally(self, user_id: int, admin_id: int) -> int:
        """Выплачено на карту вручную — обнуляем реферальный баланс."""
        user = await self.users.get(user_id, {'info.ref_stats.withdrawable': 1})
        amount = int(self.users.pick(user or {}, 'info.ref_stats.withdrawable', 0) or 0)

        if amount > 0:
            reset = await self.users.col.update_one(
                {'user_data.user_id': user_id, 'info.ref_stats.withdrawable': amount},
                {'$set': {'info.ref_stats.withdrawable': 0}})
            if reset.modified_count != 1:
                return -1

        await self._finish(user_id, 'paid_external', amount, admin_id)
        return amount

    async def reject(self, user_id: int, reason: str, admin_id: int) -> str:
        """Отказ. Реферальный баланс НЕ трогаем — деньги остаются у человека."""
        text = REJECT_REASONS.get(reason, REJECT_REASONS['other'])
        await self._finish(user_id, 'rejected', 0, admin_id, reason=reason)
        return text

    async def _finish(self, user_id: int, action: str, amount: int,
                      admin_id: int, reason: str = '') -> None:
        entry = {'action': action, 'amount': amount, 'at': now(), 'admin_id': admin_id}
        if reason:
            entry['reason'] = reason

        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.pending_payout_active': False},
             '$push': {'info.ref_stats.payout_history': entry}},
        )
        log.info('выплата %s: %s на %s₽ (админ %s)', user_id, action, amount, admin_id)
