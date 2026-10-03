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
import uuid
from dataclasses import dataclass

from app.core.time import hours_since, now
from app.domain import payout_methods as methods

log = logging.getLogger(__name__)

# Больше способов человеку не нужно, а бесконечный список ломает клавиатуру
MAX_METHODS = 5

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
    method_details: str = ''  # готовая карточка реквизитов для админ-чата


class PayoutService:
    def __init__(self, users, settings, notifier=None):
        self.users = users
        self.settings = settings
        self.notifier = notifier

    async def check(self, user_id: int) -> PayoutRequest:
        """Можно ли сейчас оформить заявку. Ничего не меняет.

        Отдельно от request(), потому что экран подтверждения обязан знать
        ответ заранее: подтверждать, чтобы потом получить «нельзя», — то же
        самое, что не спрашивать вовсе.
        """
        if not await self.settings.flag('features.payouts_enabled'):
            return PayoutRequest(False, 'disabled')

        user = await self.users.get(user_id, {'info.ref_stats': 1})
        stats = self.users.pick(user or {}, 'info.ref_stats', {}) or {}
        amount = int(stats.get('withdrawable', 0) or 0)
        selected = stats.get('payout_selected') or methods.BOT_BALANCE

        minimum = await self.settings.int('payout.min_withdraw')
        if amount < minimum:
            return PayoutRequest(False, 'below_min', amount=amount, method=selected)

        if stats.get('pending_payout_active'):
            return PayoutRequest(False, 'pending', amount=amount, method=selected)

        cooldown = await self.settings.int('payout.cooldown_hours')
        last = stats.get('last_payout_request_at')
        if last and cooldown:
            passed = hours_since(last)
            if passed < cooldown:
                return PayoutRequest(False, 'cooldown', amount=amount, method=selected,
                                     wait_hours=round(cooldown - passed, 1))

        return PayoutRequest(True, amount=amount, method=selected)

    async def request(self, user_id: int) -> PayoutRequest:
        checked = await self.check(user_id)
        if not checked.ok:
            return checked

        user = await self.users.get(user_id, {'info.ref_stats': 1})
        stats = self.users.pick(user or {}, 'info.ref_stats', {}) or {}
        amount = int(stats.get('withdrawable', 0) or 0)

        # Метку ставим атомарно: двойной клик не создаст две заявки
        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             'info.ref_stats.pending_payout_active': {'$ne': True}},
            {'$set': {'info.ref_stats.pending_payout_active': True,
                      'info.ref_stats.last_payout_request_at': now()}},
        )
        if claimed.modified_count != 1:
            return PayoutRequest(False, 'pending', amount=amount)

        selected = stats.get('payout_selected') or methods.BOT_BALANCE
        return PayoutRequest(True, amount=amount, method=selected,
                             method_details=self.describe(stats, selected))

    # ── способы вывода (реквизиты) ──────────────────────────────────────────
    #
    # Хранятся в info.ref_stats.method — там же, где их искал старый бот,
    # поэтому уже добавленные реквизиты видны сразу, без миграции.
    async def methods(self, user_id: int) -> list[dict]:
        user = await self.users.get(user_id, {'info.ref_stats.method': 1})
        saved = self.users.pick(user or {}, 'info.ref_stats.method', []) or []
        return [m for m in saved if isinstance(m, dict) and m.get('id')]

    async def draft(self, user_id: int) -> dict:
        user = await self.users.get(user_id, {'info.ref_stats.method_draft': 1})
        saved = self.users.pick(user or {}, 'info.ref_stats.method_draft')
        return saved if isinstance(saved, dict) and saved.get('type') else methods.new_draft()

    async def save_draft(self, user_id: int, draft: dict) -> None:
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.method_draft': draft}})

    async def set_draft_type(self, user_id: int, type_code: str) -> dict:
        """Смена типа обнуляет поля: у СБП и карты они разные."""
        if type_code not in methods.BY_CODE:
            type_code = methods.DEFAULT_TYPE
        draft = methods.new_draft(type_code)
        await self.save_draft(user_id, draft)
        return draft

    async def set_draft_field(self, user_id: int, field: str, value: str) -> dict:
        draft = await self.draft(user_id)
        if field in {f.code for f in methods.fields_of(draft.get('type', ''))}:
            draft.setdefault('data', {})[field] = value.strip()
            await self.save_draft(user_id, draft)
        return draft

    async def clear_draft(self, user_id: int) -> dict:
        current = await self.draft(user_id)
        draft = methods.new_draft(current.get('type') or methods.DEFAULT_TYPE)
        await self.save_draft(user_id, draft)
        return draft

    async def add_method(self, user_id: int) -> tuple[bool, str]:
        """Сохранить черновик как способ выплаты. (успех, причина отказа)."""
        draft = await self.draft(user_id)
        if not methods.is_ready(draft):
            missing = ', '.join(f.title for f in methods.missing_fields(draft))
            return False, f'Не заполнено: {missing}'

        if len(await self.methods(user_id)) >= MAX_METHODS:
            return False, f'Больше {MAX_METHODS} способов не сохранить — удалите лишний.'

        method = {'id': uuid.uuid4().hex[:12], 'type': draft['type'],
                  'data': dict(draft.get('data') or {}), 'created_at': now()}
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$push': {'info.ref_stats.method': method},
             '$set': {'info.ref_stats.method_draft': methods.new_draft(draft['type']),
                      'info.ref_stats.payout_selected': method['id']}})
        return True, ''

    async def delete_method(self, user_id: int, method_id: str) -> bool:
        remaining = [m for m in await self.methods(user_id) if m.get('id') != method_id]
        result = await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.method': remaining}})

        # выбран был удалённый способ — иначе заявка уехала бы «в никуда»
        user = await self.users.get(user_id, {'info.ref_stats.payout_selected': 1})
        if self.users.pick(user or {}, 'info.ref_stats.payout_selected') == method_id:
            await self.select_method(user_id, methods.BOT_BALANCE)
        return result.modified_count == 1

    async def select_method(self, user_id: int, method_id: str) -> str:
        """Выбрать способ для следующей заявки. Неизвестный — на баланс бота."""
        if method_id != methods.BOT_BALANCE:
            known = {m['id'] for m in await self.methods(user_id)}
            if method_id not in known:
                method_id = methods.BOT_BALANCE

        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.payout_selected': method_id}})
        return method_id

    def describe(self, stats: dict, selected: str, mask: bool = False) -> str:
        """Карточка выбранного способа. По умолчанию без маски — для админа."""
        if not selected or selected == methods.BOT_BALANCE:
            return methods.BOT_BALANCE_TITLE
        found = methods.find(stats.get('method'), selected)
        return methods.format_details(found, mask=mask) if found else methods.BOT_BALANCE_TITLE

    async def cancel_request(self, user_id: int) -> None:
        """Полный откат заявки — например, если она не дошла до админов.

        Вместе с меткой снимается и отметка времени: она ставится при захвате
        и запускает суточную паузу. Оставить её здесь значит заблокировать
        человека на сутки за заявку, которой не было.
        """
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'info.ref_stats.pending_payout_active': False},
             '$unset': {'info.ref_stats.last_payout_request_at': ''}})

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

        # Перевод реферального баланса на обычный идёт своим запросом мимо
        # users.credit(): для оператора это такие же деньги, и в истории они
        # должны стоять рядом с остальными.
        await self.users.record_money(
            user_id, -amount, 'Перевод на баланс бота: списано с реферального',
            kind='payout', admin_id=admin_id, account='referral', balance_after=0)
        await self.users.record_money(
            user_id, amount, 'Перевод реферального баланса на баланс бота',
            kind='payout', admin_id=admin_id,
            balance_after=await self.users.balance_of(user_id))
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

        # На баланс бота эти деньги не попадают — уходят на карту. Строка
        # в журнале с нулевой суммой, но с фактом: иначе «куда делись 540₽
        # с реферального» останется без ответа.
        await self.users.record_money(
            user_id, -amount, 'Выплата по реквизитам', kind='payout',
            admin_id=admin_id, account='referral', balance_after=0,
            meta={'external': True})
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
