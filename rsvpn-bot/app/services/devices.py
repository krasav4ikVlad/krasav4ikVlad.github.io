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
from app.services import bypass
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)

CHARGE_PERIOD_DAYS = 30


def remove_from_nearest_charge(packages: list[dict], amount: int) -> tuple[list[dict], int]:
    """Снять `amount` устройств, начиная с пакета с ближайшим списанием."""
    items = [dict(p) for p in packages]
    order = sorted(
        (i for i, p in enumerate(items)
         if p.get('active', True) and int(p.get('amount', 0) or 0) > 0),
        key=lambda i: parse_dt(items[i].get('nextChargeAt')) or now(),
    )

    removed = 0
    left = amount
    for index in order:
        if left <= 0:
            break
        item = items[index]
        take = min(int(item.get('amount', 0) or 0), left)
        item['amount'] = int(item['amount']) - take
        if item['amount'] <= 0:
            item['active'] = False
        removed += take
        left -= take

    return items, removed


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

        if not await self.users.charge(user_id, total, f'Доп. устройства: {amount} шт.',
                                       kind='devices', meta={'devices': amount}):
            balance = int(self.users.pick(user or {}, 'info.balance', 0) or 0)
            raise NotEnoughBalance(need=total, have=balance)

        uuid = self.users.pick(user or {}, 'vpn.uuid')
        try:
            if uuid:
                await self.vpn.update_subscription(uuid, device_limit=new_limit)
        except VpnPanelError:
            await self.users.credit(user_id, total, 'Возврат за доп. устройства',
                                    kind='refund')
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
        # ByPass — отдельная подписка в панели со своим лимитом. Без этого
        # человек покупает устройства, а подключить их может только к
        # основной, хотя платит за обе.
        await bypass.sync_devices(self.users, self.vpn, user_id, new_limit)
        log.info('%s купил %s доп. устройств за %s₽', user_id, amount, total)
        return new_limit

    async def remove(self, user_id: int, amount: int) -> int:
        """Уменьшить лимит. Снимаем с пакета, у которого ближайшее списание —
        так человек дольше пользуется уже оплаченным.

        Опорная величина — сам лимит, а не сумма пакетов. В боевой базе они
        часто расходятся: лимит подняли из панели вручную, пакет купили в
        старом боте, документ правили руками. Считать лимит как
        «база + сумма пакетов» здесь нельзя двояко: при пустом списке пакетов
        кнопка вообще ничего не делала (лимит 17 так и оставался 17), а при
        неполном списке лимит обрушивался до суммы пакетов — человек платил
        за восемь устройств и получал три.
        """
        if amount <= 0:
            raise ValueError('количество должно быть больше нуля')

        user = await self.users.get(user_id, {'vpn': 1})
        vpn = (user or {}).get('vpn') or {}
        base_limit = await self.settings.int('price.devices_free_limit')
        current = int(vpn.get('hwidDeviceLimit') or base_limit)

        # ниже бесплатного лимита не опускаемся
        amount = min(amount, max(0, current - base_limit))
        if amount <= 0:
            return current

        new_limit = current - amount
        updated, _ = remove_from_nearest_charge(list(vpn.get('extraDevices') or []), amount)

        uuid = vpn.get('uuid')
        if uuid:
            await self.vpn.update_subscription(uuid, device_limit=new_limit)

        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'vpn.extraDevices': updated, 'vpn.hwidDeviceLimit': new_limit}})
        await bypass.sync_devices(self.users, self.vpn, user_id, new_limit)

        log.info('%s уменьшил лимит на %s, стало %s', user_id, amount, new_limit)
        return new_limit

    async def subscriptions(self, user_id: int) -> list[str]:
        """UUID подписок пользователя в панели: основная и ByPass."""
        user = await self.users.get(user_id, {'vpn.uuid': 1, 'vpn.bypass_uuid': 1})
        return [uuid for uuid in (self.users.pick(user or {}, 'vpn.uuid'),
                                  self.users.pick(user or {}, 'vpn.bypass_uuid')) if uuid]

    async def bound(self, user_id: int) -> list[tuple[str, dict]]:
        """Привязанные устройства парами (uuid подписки, устройство).

        Список живёт только в Remnawave — в документе пользователя устройств
        нет и не должно быть. Пара нужна, чтобы отвязывать каждое у той
        подписки, к которой оно на самом деле привязано.
        """
        found: list[tuple[str, dict]] = []
        for uuid in await self.subscriptions(user_id):
            try:
                for device in await self.vpn.devices(uuid):
                    found.append((uuid, device))
            except VpnPanelError as exc:
                log.warning('устройства %s не получены: %s', uuid, exc)
        return found

    async def unbind(self, user_id: int, hwid: str) -> bool:
        """Отвязать одно устройство у той подписки, где оно числится.

        Раньше удаление отправлялось в обе подписки подряд, и на «чужой»
        панель отвечала 404 A204 — в логах это выглядело как ошибка при
        каждой успешной отвязке.
        """
        for uuid, device in await self.bound(user_id):
            if device.get('hwid') == hwid:
                return await self.vpn.delete_device(uuid, hwid)
        return False

    async def unbind_all(self, user_id: int) -> int:
        """Отвязать все устройства сразу. Возвращает, сколько снято.

        Лимит не трогаем: человек освобождает слоты, а не отказывается от
        оплаченных устройств.
        """
        removed = 0
        for uuid, device in await self.bound(user_id):
            hwid = device.get('hwid')
            if hwid and await self.vpn.delete_device(uuid, hwid):
                removed += 1

        log.info('у %s отвязано устройств: %s', user_id, removed)
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
            # списание идёт одним запросом мимо users.charge(), поэтому в
            # журнал пишем здесь: для человека это списание «само собой»
            await self.users.log(user_id, self.users.ACTION_AUTO,
                                 f'−{total}₽ Продление {amount} доп. устройств')
            await self.users.record_money(
                user_id, -total, f'Продление {amount} доп. устройств',
                kind='devices', auto=True,
                balance_after=await self.users.balance_of(user_id),
                meta={'devices': amount, 'package_id': package.get('id')})
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
        await bypass.sync_devices(self.users, self.vpn, user_id, new_limit)
        report.deactivated += 1

        if self.notifier:
            await self.notifier.devices_removed(user_id, amount=removed, limit=new_limit)

        log.info('у %s отключено %s доп. устройств, лимит %s', user_id, removed, new_limit)
