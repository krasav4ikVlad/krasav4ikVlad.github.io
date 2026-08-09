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


_MISSING = object()

# Ключи, под которыми панель прячет сам объект пользователя. Разные версии
# Remnawave отвечают то плоско, то `{response: {...}}`, то ещё на уровень
# глубже — `{response: {user: {...}}}`. Клиент снимает только внешний слой,
# остальное разбираем здесь.
NESTED_KEYS = ('user', 'response', 'data')


def _unwrap(payload) -> dict:
    """Достать объект пользователя из любой вложенности ответа панели."""
    current = payload
    for _ in range(4):                      # глубже четырёх слоёв не бывает
        # часть ручек отвечает списком из одного пользователя
        if isinstance(current, list):
            current = current[0] if len(current) == 1 else {}
        if not isinstance(current, dict):
            return {}
        if 'uuid' in current or 'usedTrafficBytes' in current:
            return current
        nested = next((current[key] for key in NESTED_KEYS
                       if isinstance(current.get(key), (dict, list))), None)
        if nested is None:
            return current
        current = nested
    return current if isinstance(current, dict) else {}


def _first(data: dict, *keys):
    """Первое непустое поле из перечисленных.

    Ноль — это значение, а не пропуск: на новом сервере трафика честно ноль,
    и путать его с «панель такого поля не прислала» нельзя, иначе рабочий
    сервер отмечается ошибкой. Поэтому ноль запоминается и возвращается,
    только если непустого значения не нашлось вовсе.
    """
    zero = _MISSING
    for key in keys:
        value = (data or {}).get(key)
        if value in (None, ''):
            continue
        if value:
            return value
        if zero is _MISSING:
            zero = value
    return None if zero is _MISSING else zero


# Имена, под которыми панель отдаёт израсходованный трафик. Первым идёт
# userTraffic — так называет его Remnawave 2.x, и именно из-за этого имени
# статистика показывала нули: остальные варианты в ответе просто отсутствуют.
TRAFFIC_KEYS = ('userTraffic', 'usedTrafficBytes', 'lifetimeUsedTrafficBytes',
                'usedTraffic', 'trafficUsedBytes')

# Время последнего подключения панель отдаёт не всегда и под разными именами.
ONLINE_KEYS = ('onlineAt', 'lastConnectedAt', 'subLastOpenedAt', 'lastOnlineAt')


def _bytes(value):
    """Число байт из чего угодно: числа, строки, разбивки по нодам.

    userTraffic в разных сборках панели — то число, то объект с итогом, то
    список по нодам. Разбирать одну форму значит вернуться сюда на следующей
    версии, поэтому берём любую и складываем.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        return int(value) if value.strip().isdigit() else None
    if isinstance(value, dict):
        for key in ('total', 'totalBytes', 'usedTrafficBytes', 'bytes', 'sum'):
            found = _bytes(value.get(key))
            if found is not None:
                return found
        parts = [_bytes(item) for item in value.values()]
    elif isinstance(value, list):
        parts = [_bytes(item) for item in value]
    else:
        return None

    parts = [part for part in parts if part is not None]
    return sum(parts) if parts else None


def _traffic_of(panel: dict):
    for key in TRAFFIC_KEYS:
        if key in panel:
            found = _bytes(panel[key])
            if found is not None:
                return found
    return None


# Как в записи расхода по ноде называют пользователя и объём
USER_KEYS = ('userUuid', 'uuid', 'user_uuid')
NAME_KEYS = ('username', 'userName', 'name')
TOTAL_KEYS = ('total', 'totalBytes', 'usedBytes', 'usedTrafficBytes', 'bytes')


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
    async def request(self, user_id: int, plan_code: str, location: str = '',
                      profile: str = '', title: str = '') -> Result:
        if not await self.settings.flag('private.enabled'):
            return Result(False, 'disabled')

        plan = ps.BY_CODE.get(plan_code)
        if not plan:
            return Result(False, 'unknown_plan')

        place = ps.BY_LOCATION.get(location)
        if not place:
            return Result(False, 'unknown_location')
        if profile not in ps.BY_PROFILE:
            profile = ps.DEFAULT_PROFILE

        if await self.servers.of_owner(user_id):
            return Result(False, 'already_has')

        amount = await self.price(plan)
        if not await self.users.charge(user_id, amount,
                                       f'Личный сервер «{plan.title}»'):
            return Result(False, 'no_funds', amount=amount)

        try:
            server = await self.servers.create(user_id, plan, title or place.title,
                                               location=place.code, profile=profile,
                                               traffic_gb=place.traffic_gb)
        except Exception:
            await self.users.credit(user_id, amount, 'Возврат: заявка на сервер не создана')
            raise

        # Цену фиксируем на момент покупки: подняли прайс — у тех, кто уже
        # платит, ничего не меняется, пока они не пересоздадут сервер.
        await self.servers.set(server['_id'], price=amount)
        log.info('заявка на личный сервер %s: %s, тариф %s, %s, %s, %s₽',
                 server['_id'], user_id, plan.code, place.code, profile, amount)
        return Result(True, server=dict(server, price=amount), amount=amount)

    async def activate(self, server_id: str, squad_uuid: str) -> Result:
        """Админ поднял VPS и привязал сквад — сервер начинает работать."""
        server = await self.servers.get(server_id)
        if not server:
            return Result(False, 'not_found')
        if server.get('status') != ps.REQUESTED:
            return Result(False, 'wrong_status', server=server)

        paid_until = now() + timedelta(days=ps.CHARGE_PERIOD_DAYS)
        await self.servers.set(server_id, status=ps.ACTIVE, squad_uuid=squad_uuid,
                               activated_at=now(),
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
        fresh = await self.servers.get(server['_id'])

        # Владелец раздал ссылку и ушёл: без сообщения он узнаёт о новом
        # участнике, только если сам зайдёт и пересчитает места.
        await self._tell(
            server['owner_id'],
            f'{e("friends")} <b>{await self._name(user_id)}</b> присоединился '
            f'к серверу «{server.get("title")}».\n'
            f'Занято {ps.occupied(fresh)} из {fresh.get("slots")} мест.')
        return Result(True, server=fresh)

    async def kick(self, server_id: str, owner_id: int, user_id: int) -> Result:
        server = await self.servers.get(server_id)
        if not server or server.get('owner_id') != owner_id:
            return Result(False, 'not_owner')
        if not await self.servers.remove_member(server_id, user_id):
            return Result(False, 'not_member', server=server)

        await self._revoke(server, user_id)
        # Доступ пропал молча — человек решит, что сломался VPN, и пойдёт
        # в поддержку. Пусть знает, что это решение владельца.
        await self._tell(user_id,
                         f'{e("cross")} Владелец закрыл вам доступ к серверу '
                         f'«{server.get("title")}».')
        return Result(True, server=await self.servers.get(server_id))

    async def leave(self, server_id: str, user_id: int) -> Result:
        server = await self.servers.get(server_id)
        if not server or not await self.servers.remove_member(server_id, user_id):
            return Result(False, 'not_member', server=server)

        await self._revoke(server, user_id)
        fresh = await self.servers.get(server_id)
        await self._tell(
            server['owner_id'],
            f'{e("minus")} <b>{await self._name(user_id)}</b> вышел с сервера '
            f'«{server.get("title")}». Свободных мест: {ps.free_slots(fresh)}.')
        return Result(True, server=server)

    # ── статистика ──────────────────────────────────────────────────────────
    async def _server_usage(self, server: dict) -> tuple[dict, str]:
        """Расход участников именно на этом сервере.

        Ключи — и числовой id из панели, и username: сопоставить можно любым,
        а какой придёт, зависит от того, какой ручкой считали.

        Панели новее отдают расход прямо по скваду. У тех, что старше, такой
        ручки нет (404), и тогда считаем по нодам сквада — это те же цифры,
        просто в два запроса.
        """
        squad = server.get('squad_uuid')
        if not squad:
            return {}, 'у сервера не задан сквад'

        # Период — с запуска сервера, но не глубже оплаченного месяца.
        # Конец завтрашним днём: границы у панели по датам, и «сегодня —
        # сегодня» на свежем сервере даёт пустой диапазон.
        until = now() + timedelta(days=1)
        since = now() - timedelta(days=ps.CHARGE_PERIOD_DAYS)
        started = parse_dt(server.get('activated_at'))
        if started and started > since:
            since = started

        try:
            by_squad = await self.vpn.squad_usage(squad, since, until)
        except Exception as exc:
            log.warning('расход сквада %s не получен: %s', squad, exc)
            by_squad = {}
        if by_squad:
            return by_squad, ''

        try:
            nodes = await self.vpn.squad_nodes(squad)
        except Exception as exc:
            log.warning('ноды сквада %s не получены: %s', squad, exc)
            return {}, f'ноды сквада: {exc}'
        if not nodes:
            return {}, 'в скваде нет нод'

        usage: dict = {}
        note = ''
        for node in nodes:
            node_uuid = node.get('uuid') or node.get('nodeUuid')
            if not node_uuid:
                continue
            try:
                for name, total in (await self.vpn.node_users_usage(
                        node_uuid, since, until)).items():
                    usage[name] = usage.get(name, 0) + total
            except Exception as exc:
                note = f'расход ноды: {exc}'
                log.warning('расход ноды %s не получен: %s', node_uuid, exc)

        if not usage and not note:
            note = 'панель вернула пустой расход по нодам'
        return usage, note

    async def stats(self, server: dict) -> list[dict]:
        """Трафик и последнее подключение по каждому участнику.

        Панель опрашивается по одному человеку: участников максимум
        пятнадцать, а отдельного пакетного метода в API нет.
        """
        rows = []
        by_server, usage_note = await self._server_usage(server)
        people = [server.get('owner_id')] + [m.get('user_id')
                                             for m in (server.get('members') or [])]
        for user_id in [p for p in people if p]:
            user = await self.users.get(user_id, {'user_data': 1, 'vpn.uuid': 1})
            row = {'user_id': user_id,
                   'name': self.users.pick(user or {}, 'user_data.first_name') or user_id,
                   'username': self.users.pick(user or {}, 'user_data.username') or '',
                   'owner': user_id == server.get('owner_id'),
                   'traffic': 0, 'online_at': None, 'status': '', 'error': ''}
            uuid = self.users.pick(user or {}, 'vpn.uuid')
            if not uuid:
                # Ноль без объяснения читается как «трафика нет», хотя на
                # самом деле нам нечего было спрашивать у панели.
                row['error'] = 'нет подписки в панели'
                rows.append(row)
                continue

            try:
                panel = _unwrap(await self.vpn.get_subscription(uuid))
            except Exception as exc:          # панель недоступна — не рушим экран
                row['error'] = str(exc)
                log.warning('статистика %s не получена: %s', user_id, exc)
                rows.append(row)
                continue

            # Сначала расход именно на этом сервере, и только если панель
            # его не дала — общий трафик из карточки. Он больше настоящего,
            # и экран об этом честно предупреждает.
            panel_id = panel.get('id')
            # Совпасть может по любому из двух: id даёт ручка сквада,
            # username — ручка ноды.
            scoped = None
            for key in (int(panel_id) if isinstance(panel_id, (int, float)) else None,
                        str(panel.get('username') or ''), str(user_id)):
                if key not in (None, '') and key in by_server:
                    scoped = by_server[key]
                    break
            traffic = scoped if scoped is not None else _traffic_of(panel)
            row['source'] = 'server' if scoped is not None else 'user'
            row['panel_id'] = panel_id
            if scoped is None and usage_note:
                # Почему не вышло посчитать по ноде — видно в /srvdiag, а не
                # только в логах на сервере.
                row['usage_note'] = usage_note
            row['traffic'] = traffic or 0
            # onlineAt лежит внутри userTraffic, а не рядом с ним: сверху
            # его искать бесполезно, что и давало вечное «не заходил».
            inner = panel.get('userTraffic') if isinstance(panel.get('userTraffic'),
                                                           dict) else {}
            row['online_at'] = parse_dt(_first(inner, *ONLINE_KEYS)
                                        or _first(panel, *ONLINE_KEYS))
            row['online_known'] = any(key in inner or key in panel
                                      for key in ONLINE_KEYS)
            row['status'] = panel.get('status') or ''
            row['devices'] = panel.get('hwidDeviceLimit')
            row['fields'] = sorted(panel)[:40]
            # Поля про трафик отдельно: по ним видно, как эта версия панели
            # его называет, без чтения всего ответа.
            row['traffic_fields'] = {key: value for key, value in panel.items()
                                     if 'raffic' in key.lower()}
            row['raw'] = str(panel)[:600]

            if traffic is None:
                row['error'] = 'панель не отдала трафик'
                log.warning('панель по %s не отдала трафик, поля ответа: %s',
                            user_id, row['fields'])
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

    async def _name(self, user_id: int) -> str:
        user = await self.users.get(user_id, {'user_data': 1})
        return (self.users.pick(user or {}, 'user_data.first_name')
                or f'id {user_id}')

    async def _tell(self, user_id: int, text: str) -> None:
        if not self.bot:
            return
        try:
            await self.bot.send_message(user_id, text)
        except Exception as exc:
            log.info('владелец %s не уведомлён: %s', user_id, exc)
