"""Автопродление подписки списанием с баланса.

Перенос из utils.process_subscriptions. Напоминания оттуда ушли в вебхуки
(services/expiry.py), здесь осталось только списание — и порядок действий
переставлен так, чтобы деньги и подписка не расходились:

  было:  обновили панель → посчитали новый баланс в питоне → записали его
  стало: списали атомарно → обновили панель → при ошибке вернули деньги

Почему это важно:

* `'$set': {'info.balance': balance - price}` затирает параллельные изменения.
  Если между чтением и записью человек пополнил баланс или купил устройства,
  результат этой операции пропадал. `users.charge()` делает `$inc` с проверкой
  `$gte` внутри одного запроса;
* панель обновлялась ДО списания, и при падении записи в Mongo продление
  доставалось бесплатно;
* продление срабатывало только при `0 < hours_left < 24`. Если бот полежал
  сутки, окно проскакивало, и подписка молча умирала при живом балансе.
  Теперь просроченные тоже продлеваются — в пределах grace-окна.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)


@dataclass
class RenewalReport:
    checked: int = 0
    renewed: int = 0
    charged: int = 0
    no_funds: int = 0
    failed: int = 0
    notes: dict[str, int] = field(default_factory=dict)

    def note(self, key: str) -> None:
        self.notes[key] = self.notes.get(key, 0) + 1


class RenewalService:
    def __init__(self, users, plans, settings, vpn, topup,
                 lifeline=None, expiry=None, notifier=None):
        self.users = users
        self.plans = plans
        self.settings = settings
        self.vpn = vpn
        self.topup = topup
        self.lifeline = lifeline
        self.expiry = expiry
        self.notifier = notifier

    async def run(self) -> RenewalReport:
        report = RenewalReport()
        if not await self.settings.flag('features.autorenew_enabled'):
            log.info('автопродление выключено в админке')
            return report

        window = await self.settings.int('renewal.window_hours')
        grace = await self.settings.int('renewal.grace_hours')
        moment = now()
        border = moment + timedelta(hours=window)

        async for user in self.users.iterate(
            {'vpn.shortUuid': {'$ne': ''}, 'vpn.expireAt': {'$lte': border}},
            {'user_data.user_id': 1, 'info.balance': 1, 'vpn': 1},
        ):
            report.checked += 1
            expires = parse_dt(self.users.pick(user, 'vpn.expireAt'))
            if not expires:
                report.note('no_expire_date')
                continue

            hours_left = (expires - moment).total_seconds() / 3600
            if hours_left < -grace:
                # ушла слишком давно — это работа возвратных кампаний, не биллинга
                report.note('too_old')
                continue

            note = await self.renew(user, expires)
            report.note(note)
            if note == 'renewed':
                report.renewed += 1
            elif note == 'no_funds':
                report.no_funds += 1
            elif note.startswith('error'):
                report.failed += 1

        log.info('автопродление: проверено=%s продлено=%s без средств=%s ошибок=%s',
                 report.checked, report.renewed, report.no_funds, report.failed)
        return report

    async def renew(self, user: dict, expires) -> str:
        user_id = self.users.pick(user, 'user_data.user_id')
        vpn = user.get('vpn') or {}
        uuid = vpn.get('uuid')

        if not user_id or not uuid:
            return 'no_uuid'

        plan = await self.plans.by_days(vpn.get('period') or 0)
        if not plan:
            return 'unknown_plan'

        # Только цена тарифа. Плату за доп. устройства берёт DeviceBillingService
        # раз в 30 дней со своих пакетов — прибавлять её здесь значит списать
        # за устройства дважды, а на дневном тарифе ещё и каждый день.
        price = int(plan['price'])

        # 1. Деньги. Проверка баланса живёт внутри запроса, поэтому параллельная
        #    покупка не может увести баланс в минус, а её результат — потеряться.
        if not await self.users.charge(user_id, price, f'Продление подписки «{plan["title"]}»'):
            return 'no_funds'

        # 2. Панель. Считаем от текущей даты, если подписка уже просрочена, —
        #    иначе продление «в прошлое» и человек остаётся без доступа.
        base = expires if expires > now() else now()
        new_expire = base + timedelta(days=int(plan['days']))

        try:
            await self.vpn.update_subscription(uuid, expire_at=new_expire)
        except VpnPanelError as exc:
            await self.users.credit(user_id, price, 'Возврат: панель не продлила подписку')
            log.error('продление %s не удалось, деньги возвращены: %s', user_id, exc)
            return 'error_panel'

        # ByPass — отдельная подписка в панели, обновляем только если она есть.
        # В оригинале PATCH уходил и с пустым uuid, а результат не проверялся.
        bypass_uuid = vpn.get('bypass_uuid')
        if bypass_uuid:
            try:
                await self.vpn.update_subscription(bypass_uuid, expire_at=new_expire)
            except VpnPanelError as exc:
                log.warning('bypass %s не продлён: %s', user_id, exc)

        fields = {'expireAt': new_expire}
        if bypass_uuid:
            fields['bypass_expireAt'] = new_expire
        await self.users.set_vpn(user_id, fields)

        # 3. Последействия: сбросить напоминания и вернуть родные серверы
        if self.expiry:
            await self.expiry.reset_after_renewal(user_id)
        if self.lifeline:
            await self.lifeline.restore(user_id)
        if self.notifier:
            await self.notifier.renewed(user_id, plan=plan, price=price, until=new_expire)

        log.info('продлено %s: %s на %s дней за %s₽', user_id, plan['code'], plan['days'], price)
        return 'renewed'
