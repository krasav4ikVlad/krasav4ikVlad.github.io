"""Личные серверы: покупка, участники, продление.

Доступ участника — это внутренний сквад сервера, добавленный к его
собственной подписке в панели. Так получается сразу три вещи, которых нет у
общей ссылки: у каждого свой трафик (значит, у владельца есть статистика),
любого можно отключить по отдельности, и человеку не нужно ничего
переподключать — тот же конфиг, просто появился ещё один сервер.

Членство даёт доступ само по себе: смысл тарифа в том, что друзья
скидываются вместо того, чтобы каждый платил свою подписку. Поэтому на входе
участнику продлевается срок до даты, оплаченной владельцем, а на выходе
возвращается тот, что был. Иначе выход из сервера дарил бы человеку месяц
обычной подписки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from app.content.emoji import e
from app.core.time import now, parse_dt
from app.domain import private_servers as ps

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ''
    server: dict | None = None
    amount: int = 0


class PrivateServerService:
    def __init__(self, users, servers, settings, vpn, notifier=None, bot=None):
        self.users = users
        self.servers = servers
        self.settings = settings
        self.vpn = vpn
        self.notifier = notifier
        self.bot = bot

    # ── тарифы ──────────────────────────────────────────────────────────────
    async def price(self, plan: ps.ServerPlan) -> int:
        """Цена из настроек: прайс правится из админки, а не выкладкой."""
        override = await self.settings.int(f'private.price_{plan.code}')
        return override or plan.price

    async def plans(self) -> list[tuple[ps.ServerPlan, int]]:
        return [(plan, await self.price(plan)) for plan in ps.PLANS]

    # ── покупка ─────────────────────────────────────────────────────────────
    async def request(self, user_id: int, plan_code: str, title: str = '') -> Result:
        if not await self.settings.flag('private.enabled'):
            return Result(False, 'disabled')

        plan = ps.BY_CODE.get(plan_code)
        if not plan:
            return Result(False, 'unknown_plan')

        if await self.servers.of_owner(user_id):
            return Result(False, 'already_has')

        amount = await self.price(plan)
        if not await self.users.charge(user_id, amount,
                                       f'Личный сервер «{plan.title}»'):
            return Result(False, 'no_funds', amount=amount)

        try:
            server = await self.servers.create(user_id, plan, title or plan.title)
        except Exception:
            await self.users.credit(user_id, amount, 'Возврат: заявка на сервер не создана')
            raise

        # Цену фиксируем на момент покупки: подняли прайс — у тех, кто уже
        # платит, ничего не меняется, пока они не пересоздадут сервер.
        await self.servers.set(server['_id'], price=amount)
        log.info('заявка на личный сервер %s: %s, тариф %s, %s₽',
                 server['_id'], user_id, plan.code, amount)
        return Result(True, server=dict(server, price=amount), amount=amount)

    async def activate(self, server_id: str, squad_uuid: str, location: str = '') -> Result:
        """Админ поднял VPS и привязал сквад — сервер начинает работать."""
        server = await self.servers.get(server_id)
        if not server:
            return Result(False, 'not_found')
        if server.get('status') != ps.REQUESTED:
            return Result(False, 'wrong_status', server=server)

        paid_until = now() + timedelta(days=ps.CHARGE_PERIOD_DAYS)
        await self.servers.set(server_id, status=ps.ACTIVE, squad_uuid=squad_uuid,
                               location=location, activated_at=now(),
                               paid_until=paid_until, next_charge_at=paid_until)

        server = await self.servers.get(server_id)
        await self._grant(server, server['owner_id'])
        return Result(True, server=server)

    async def reject(self, server_id: str, reason: str = '') -> Result:
        """Отказ до запуска: деньги возвращаются целиком."""
        server = await self.servers.get(server_id)
        if not server or server.get('status') != ps.REQUESTED:
            return Result(False, 'wrong_status', server=server)

        amount = int(server.get('price') or 0)
        await self.users.credit(server['owner_id'], amount,
                                'Возврат: сервер не выдан')
        await self.servers.set(server_id, status=ps.CANCELLED, cancel_reason=reason,
                               cancelled_at=now())
        return Result(True, server=server, amount=amount)

    # ── участники ───────────────────────────────────────────────────────────
    async def invite(self, server_id: str, owner_id: int) -> Result:
        server = await self.servers.get(server_id)
        if not server or server.get('owner_id') != owner_id:
            return Result(False, 'not_owner')
        if server.get('status') != ps.ACTIVE:
            return Result(False, 'not_active', server=server)
        if ps.free_slots(server) <= 0:
            return Result(False, 'no_slots', server=server)

        code = await self.servers.add_invite(server_id)
        return Result(True, server=server, reason=code)

    async def join(self, code: str, user_id: int) -> Result:
        server = await self.servers.by_invite(code)
        if not server:
            return Result(False, 'bad_code')
        if server.get('status') != ps.ACTIVE:
            return Result(False, 'not_active', server=server)
        if server.get('owner_id') == user_id:
            return Result(False, 'own_server', server=server)
        if ps.is_member(server, user_id):
            return Result(False, 'already_member', server=server)
        if ps.free_slots(server) <= 0:
            return Result(False, 'no_slots', server=server)

        # Гасим код и занимаем слот двумя атомарными шагами: оба условия
        # проверяются внутри запросов, поэтому два человека по одной ссылке
        # не пролезут, даже если нажмут одновременно.
        if not await self.servers.use_invite(server['_id'], code, user_id):
            return Result(False, 'bad_code', server=server)

        limit = int(server.get('slots') or 0)
        # −1: владелец тоже занимает слот, но в members его нет
        if not await self.servers.add_member(server['_id'], user_id, max(1, limit - 1)):
            # Слот увели, пока мы гасили код. Возвращаем приглашение в оборот:
            # иначе владелец теряет ссылку из-за чужой гонки.
            await self.servers.release_invite(server['_id'], code)
            return Result(False, 'no_slots', server=server)

        try:
            await self._grant(server, user_id)
        except Exception:
            await self.servers.remove_member(server['_id'], user_id)
            await self.servers.release_invite(server['_id'], code)
            log.exception('доступ к серверу %s для %s не выдан', server['_id'], user_id)
            return Result(False, 'panel', server=server)

        log.info('%s присоединился к серверу %s', user_id, server['_id'])
        return Result(True, server=await self.servers.get(server['_id']))

    async def kick(self, server_id: str, owner_id: int, user_id: int) -> Result:
        server = await self.servers.get(server_id)
        if not server or server.get('owner_id') != owner_id:
            return Result(False, 'not_owner')
        if not await self.servers.remove_member(server_id, user_id):
            return Result(False, 'not_member', server=server)

        await self._revoke(server, user_id)
        return Result(True, server=await self.servers.get(server_id))

    async def leave(self, server_id: str, user_id: int) -> Result:
        server = await self.servers.get(server_id)
        if not server or not await self.servers.remove_member(server_id, user_id):
            return Result(False, 'not_member', server=server)
        await self._revoke(server, user_id)
        return Result(True, server=server)

    # ── статистика ──────────────────────────────────────────────────────────
    async def stats(self, server: dict) -> list[dict]:
        """Трафик и последнее подключение по каждому участнику.

        Панель опрашивается по одному человеку: участников максимум
        пятнадцать, а отдельного пакетного метода в API нет.
        """
        rows = []
        people = [server.get('owner_id')] + [m.get('user_id')
                                             for m in (server.get('members') or [])]
        for user_id in [p for p in people if p]:
            user = await self.users.get(user_id, {'user_data': 1, 'vpn.uuid': 1})
            row = {'user_id': user_id,
                   'name': self.users.pick(user or {}, 'user_data.first_name') or user_id,
                   'username': self.users.pick(user or {}, 'user_data.username') or '',
                   'owner': user_id == server.get('owner_id'),
                   'traffic': 0, 'online_at': None, 'status': ''}
            uuid = self.users.pick(user or {}, 'vpn.uuid')
            if uuid:
                try:
                    panel = await self.vpn.get_subscription(uuid)
                    row['traffic'] = panel.get('usedTrafficBytes') or 0
                    row['online_at'] = parse_dt(panel.get('onlineAt'))
                    row['status'] = panel.get('status') or ''
                except Exception as exc:      # панель недоступна — не рушим экран
                    log.warning('статистика %s не получена: %s', user_id, exc)
            rows.append(row)
        return rows

    # ── продление ───────────────────────────────────────────────────────────
    async def set_autorenew(self, server_id: str, owner_id: int, on: bool) -> Result:
        """Владелец решает, списывать ли следующий месяц.

        Выключенное продление ничего не отключает сразу: сервер доработает
        оплаченный месяц и закроется в дату, до которой оплачен. Иначе
        «отказаться от следующего списания» означало бы подарить нам остаток
        уже оплаченного периода.
        """
        server = await self.servers.get(server_id)
        if not server or server.get('owner_id') != owner_id:
            return Result(False, 'not_owner')

        await self.servers.set(server_id, autorenew=bool(on))
        return Result(True, server=await self.servers.get(server_id))

    async def warn_upcoming(self) -> int:
        """Предупредить владельцев о ближайшем списании. Возвращает сколько."""
        border = now() + timedelta(days=ps.WARN_DAYS)
        sent = 0
        for server in await self.servers.soon_due(border):
            owner = await self.users.get(server['owner_id'], {'info.balance': 1})
            balance = int(self.users.pick(owner or {}, 'info.balance', 0) or 0)
            price = int(server.get('price') or 0)

            await self.servers.set(server['_id'], charge_warned_at=now())
            await self._tell(
                server['owner_id'],
                f'{e("calendar")} Через {ps.WARN_DAYS} дня спишется '
                f'<b>{price}₽</b> за сервер «{server.get("title")}».\n'
                f'На балансе сейчас <b>{balance}₽</b>.'
                + ('' if balance >= price else
                   '\n\nЕсли не пополнить, сервер приостановится.')
                + '\n\nНе нужен на следующий месяц — выключите продление '
                  'в профиле, сервер доработает оплаченное и закроется.')
            sent += 1
        return sent

    async def charge_due(self) -> dict:
        """Ежемесячное списание с владельцев. Возвращает сводку для /diag."""
        report = {'checked': 0, 'charged': 0, 'amount': 0, 'suspended': 0,
                  'closed': 0, 'warned': 0}
        report['warned'] = await self.warn_upcoming()

        for server in await self.servers.due():
            report['checked'] += 1
            price = int(server.get('price') or 0)
            owner_id = server['owner_id']

            # Продление выключено самим владельцем — не списываем и закрываем:
            # оплаченный месяц он уже отработал.
            if not server.get('autorenew', True):
                await self.close(server, reason='владелец отказался от продления')
                report['closed'] += 1
                continue

            if await self.users.charge(owner_id, price,
                                       f'Продление сервера «{server.get("title")}»',
                                       auto=True):
                paid_until = (parse_dt(server.get('paid_until')) or now()) + timedelta(
                    days=ps.CHARGE_PERIOD_DAYS)
                await self.servers.set(server['_id'], paid_until=paid_until,
                                       next_charge_at=paid_until,
                                       charge_warned_at=None)
                await self._extend_everyone(await self.servers.get(server['_id']))
                report['charged'] += 1
                report['amount'] += price
                continue

            await self.suspend(server)
            report['suspended'] += 1

        # Отсрочка кончилась — закрываем и говорим админам гасить VPS
        border = now() - timedelta(days=ps.GRACE_DAYS)
        for server in await self.servers.overdue(border):
            await self.close(server)
            report['closed'] += 1

        return report

    async def suspend(self, server: dict) -> None:
        """Денег нет: доступ снимаем, VPS пока не гасим — отсрочка."""
        await self.servers.set(server['_id'], status=ps.SUSPENDED, suspended_at=now())
        for user_id in self._everyone(server):
            await self._revoke(server, user_id)

        log.warning('сервер %s приостановлен: у владельца нет %s₽',
                    server['_id'], server.get('price'))
        await self._tell(server['owner_id'],
                         f'Сервер «{server.get("title")}» приостановлен: не хватило '
                         f'{server.get("price")}₽ на балансе. Пополните — доступ '
                         f'вернётся. Через {ps.GRACE_DAYS} дня сервер закроется.')
        if self.notifier:
            await self.notifier.send('servers',
                                     f'Сервер {server["_id"]} приостановлен (нет средств)')

    async def close(self, server: dict, reason: str = 'оплата не поступила') -> None:
        await self.servers.set(server['_id'], status=ps.CANCELLED, cancelled_at=now(),
                               cancel_reason=reason)
        for user_id in self._everyone(server):
            await self._revoke(server, user_id)
        await self._tell(server['owner_id'],
                         f'Сервер «{server.get("title")}» закрыт: {reason}.')
        if self.notifier:
            await self.notifier.send(
                'payments',
                f'Сервер {server["_id"]} закрыт ({reason}) — можно гасить VPS')

    # ── работа с панелью ────────────────────────────────────────────────────
    @staticmethod
    def _everyone(server: dict) -> list[int]:
        return [server['owner_id']] + [m['user_id'] for m in (server.get('members') or [])
                                       if m.get('user_id')]

    async def _grant(self, server: dict, user_id: int) -> None:
        """Выдать доступ: сквад сервера плюс срок до оплаченной владельцем даты."""
        squad = server.get('squad_uuid')
        user = await self.users.get(user_id)
        vpn = (user or {}).get('vpn') or {}
        paid_until = parse_dt(server.get('paid_until')) or now()

        if not vpn.get('uuid'):
            created = await self.vpn.create_subscription(
                user_id, days=max(1, (paid_until - now()).days))
            await self.users.set_vpn(user_id, {
                'uuid': created['uuid'], 'shortUuid': created['shortUuid'],
                'expireAt': created['expireAt'], 'createdAt': created['createdAt'],
                'period': vpn.get('period') or 1,
            })
            user = await self.users.get(user_id)
            vpn = (user or {}).get('vpn') or {}

        squads = list(vpn.get('activeInternalSquads') or [])
        if squad and squad not in squads:
            squads.append(squad)

        expires = parse_dt(vpn.get('expireAt'))
        fields: dict = {'private_server_id': server['_id'], 'private_squad': squad,
                        'activeInternalSquads': squads}

        # Свой срок запоминаем один раз: повторный вход не должен затирать
        # его уже продлённым значением, иначе выход подарит человеку месяц.
        if expires and expires < paid_until:
            if not vpn.get('private_prev_expire'):
                fields['private_prev_expire'] = expires
            fields['expireAt'] = paid_until

        await self.vpn.update_subscription(
            vpn['uuid'], squads=squads, status='ACTIVE',
            expire_at=paid_until if expires and expires < paid_until else None)
        await self.users.set_vpn(user_id, fields)

    async def _revoke(self, server: dict, user_id: int) -> None:
        """Снять доступ и вернуть человеку его собственный срок."""
        squad = server.get('squad_uuid')
        user = await self.users.get(user_id)
        vpn = (user or {}).get('vpn') or {}
        if not vpn.get('uuid'):
            return

        squads = [s for s in (vpn.get('activeInternalSquads') or []) if s != squad]
        previous = parse_dt(vpn.get('private_prev_expire'))

        try:
            await self.vpn.update_subscription(vpn['uuid'], squads=squads,
                                               expire_at=previous)
        except Exception as exc:
            log.warning('доступ %s к серверу %s не снят: %s', user_id, server['_id'], exc)
            return

        fields = {'activeInternalSquads': squads,
                  'private_server_id': '', 'private_squad': '',
                  'private_prev_expire': None}
        if previous:
            fields['expireAt'] = previous
        await self.users.set_vpn(user_id, fields)

    async def _extend_everyone(self, server: dict) -> None:
        """После оплаты месяца — продлить срок всем, кто живёт на сервере."""
        for user_id in self._everyone(server):
            try:
                await self._grant(server, user_id)
            except Exception:
                log.exception('срок участника %s не продлён (сервер %s)',
                              user_id, server['_id'])

    async def _tell(self, user_id: int, text: str) -> None:
        if not self.bot:
            return
        try:
            await self.bot.send_message(user_id, text)
        except Exception as exc:
            log.info('владелец %s не уведомлён: %s', user_id, exc)
