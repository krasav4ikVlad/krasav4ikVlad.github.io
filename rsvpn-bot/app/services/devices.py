"""Ежемесячная плата за дополнительные устройства.

Перенос из utils._process_devices_monthly_charge с исправлением двух вещей.

1. Позиционный оператор `$` бил не в тот элемент массива. Условия писались
   так:

       {'vpn.extraDevices.id': item['id'],
        'vpn.extraDevices.nextChargeAt': item['nextChargeAt'],
        'vpn.extraDevices.active': True}

   Mongo проверяет каждое условие по массиву целиком, а не по одному элементу:
   документ подходит, если id совпал у первого пакета, дата — у второго,
   а active — у третьего. Дальше `$` обновляет первый совпавший элемент, то
   есть можно сдвинуть дату списания чужому пакету. Лечится `$elemMatch`.

2. Базовый лимит устройств был зашит числом 2 (`new_limit = 2 + sum(...)`),
   хотя рядом в коде он же настраивается.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from uuid import uuid4

from app.core.errors import NotEnoughBalance, VpnPanelError
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)

CHARGE_PERIOD_DAYS = 30


@dataclass
class DeviceBillingReport:
    charged: int = 0
    amount: int = 0
    deactivated: int = 0
    skipped: int = 0


class DeviceBillingService:
    def __init__(self, users, settings, vpn, notifier=None):
        self.users = users
        self.settings = settings
        self.vpn = vpn
        self.notifier = notifier

    async def add(self, user_id: int, amount: int) -> int:
        """Купить пакет доп. устройств. Возвращает новый лимит.

        Порядок тот же, что и в продлении: сначала деньги, потом панель,
        при отказе панели — возврат.
        """
        if amount <= 0:
            raise ValueError('количество должно быть больше нуля')

        price_each = await self.settings.int('price.device_extra')
        total = amount * price_each

        user = await self.users.get(user_id, {'vpn': 1})
        current = int(self.users.pick(user or {}, 'vpn.hwidDeviceLimit',
                                      await self.settings.int('price.devices_free_limit')))
        new_limit = current + amount

        if not await self.users.charge(user_id, total, f'Доп. устройства: {amount} шт.'):
            balance = int(self.users.pick(user or {}, 'info.balance', 0) or 0)
            raise NotEnoughBalance(need=total, have=balance)

        uuid = self.users.pick(user or {}, 'vpn.uuid')
        try:
            if uuid:
                await self.vpn.update_subscription(uuid, device_limit=new_limit)
        except VpnPanelError:
            await self.users.credit(user_id, total, 'Возврат за доп. устройства')
            raise

        package = {
            'id': uuid4().hex[:12], 'amount': amount, 'pricePerDevice': price_each,
            'active': True, 'createdAt': now(),
            'nextChargeAt': now() + timedelta(days=CHARGE_PERIOD_DAYS),
        }
        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$push': {'vpn.extraDevices': package},
             '$set': {'vpn.hwidDeviceLimit': new_limit}},
        )
        log.info('%s купил %s доп. устройств за %s₽', user_id, amount, total)
        return new_limit

    async def unbind(self, user_id: int, hwid: str) -> bool:
        """Отвязать устройство от обеих подписок — основной и ByPass."""
        user = await self.users.get(user_id, {'vpn.uuid': 1, 'vpn.bypass_uuid': 1})
        removed = False
        for field in ('vpn.uuid', 'vpn.bypass_uuid'):
            uuid = self.users.pick(user or {}, field)
            if uuid and await self.vpn.delete_device(uuid, hwid):
                removed = True
        return removed

    async def run(self) -> DeviceBillingReport:
        report = DeviceBillingReport()
        if not await self.settings.flag('features.devices_enabled'):
            return report

        moment = now()
        async for user in self.users.iterate(
            {'vpn.extraDevices.0': {'$exists': True}},
            {'user_data.user_id': 1, 'info.balance': 1, 'vpn': 1},
        ):
            for package in (user.get('vpn') or {}).get('extraDevices') or []:
                await self._charge_package(user, package, moment, report)
        return report

    async def _charge_package(self, user: dict, package: dict, moment, report) -> None:
        if not package.get('active', True):
            return

        amount = int(package.get('amount', 0) or 0)
        if amount <= 0:
            return

        due = parse_dt(package.get('nextChargeAt'))
        if not due or moment < due:
            report.skipped += 1
            return

        user_id = self.users.pick(user, 'user_data.user_id')
        price_each = int(package.get('pricePerDevice')
                         or await self.settings.int('price.device_extra'))
        total = amount * price_each

        # Списание и сдвиг даты — одним запросом. $elemMatch гарантирует, что
        # все три условия выполнены на ОДНОМ элементе массива, а `$` попадёт
        # именно в него.
        charged = await self.users.col.update_one(
            {
                '_id': user['_id'],
                'info.balance': {'$gte': total},
                'vpn.extraDevices': {'$elemMatch': {
                    'id': package.get('id'),
                    'nextChargeAt': package.get('nextChargeAt'),
                    'active': True,
                }},
            },
            {
                '$inc': {'info.balance': -total},
                '$set': {'vpn.extraDevices.$.nextChargeAt': due + timedelta(days=CHARGE_PERIOD_DAYS)},
                '$push': {'info.transactions': {
                    'amount': -total, 'dt': moment,
                    'description': f'Продление {amount} доп. устройств',
                }},
            },
        )

        if charged.modified_count == 1:
            report.charged += 1
            report.amount += total
            if self.notifier:
                await self.notifier.devices_charged(user_id, amount=amount, price=total,
                                                    next_charge=due + timedelta(days=CHARGE_PERIOD_DAYS))
            return

        await self._deactivate(user, package, amount, report)

    async def _deactivate(self, user: dict, package: dict, amount: int, report) -> None:
        """Денег не хватило — выключаем пакет и уменьшаем лимит в панели."""
        user_id = self.users.pick(user, 'user_data.user_id')
        fresh = await self.users.get(user_id, {'vpn.extraDevices': 1, 'vpn.uuid': 1})
        packages = (fresh or {}).get('vpn', {}).get('extraDevices') or []

        updated = []
        removed = 0
        for item in packages:
            if item.get('id') == package.get('id') and item.get('active', True):
                item = {**item, 'active': False, 'deactivated_at': now()}
                removed += int(item.get('amount', 0) or 0)
            updated.append(item)

        if not removed:
            return

        base_limit = await self.settings.int('price.devices_free_limit')
        new_limit = base_limit + sum(int(x.get('amount', 0) or 0)
                                     for x in updated if x.get('active', True))

        uuid = self.users.pick(fresh or {}, 'vpn.uuid')
        if uuid:
            try:
                await self.vpn.update_subscription(uuid, device_limit=new_limit)
            except VpnPanelError as exc:
                # панель не приняла — не трогаем документ, попробуем в следующий раз
                log.warning('лимит устройств %s не обновлён: %s', user_id, exc)
                return

        await self.users.col.update_one(
            {'_id': user['_id']},
            {'$set': {'vpn.extraDevices': updated, 'vpn.hwidDeviceLimit': new_limit}},
        )
        report.deactivated += 1

        if self.notifier:
            await self.notifier.devices_removed(user_id, amount=removed, limit=new_limit)

        log.info('у %s отключено %s доп. устройств, лимит %s', user_id, removed, new_limit)
