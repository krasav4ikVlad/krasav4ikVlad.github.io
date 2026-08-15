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
from app.core.time import fmt, now, parse_dt
from app.domain import private_servers as ps
from app.services import bypass

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


# ── разбор ссылок подписки ──────────────────────────────────────────────────
#
# В подписке лежат не только серверы. Строки-подсказки («⬆️Обход LTE в
# описании подписки⬆️») оформлены такими же ссылками — иначе клиент их не
# покажет. Роутеру они не годятся: подключение по ним не поднимется.

# Значки, которыми размечают такие подсказки: стрелки на соседнюю строку,
# предупреждения, запреты. Заданы диапазонами, а не символами: писать значок
# в коде здесь нельзя (реестр в app/content/emoji.py — для того, что бот
# показывает, а это то, что он читает), да и перечислять их пришлось бы
# бесконечно.
BANNER_RANGES = ((0x2190, 0x21FF),      # стрелки
                 (0x2B00, 0x2BFF),      # стрелки-значки
                 (0x2600, 0x27BF))      # предупреждения, крестики, дингбаты
# Флаги (U+1F1E6…) сюда не попадают нарочно: ими подписывают как раз
# настоящие серверы.


def link_remark(link: str) -> str:
    """Подпись ссылки — то, что после решётки. Обычно percent-encoded."""
    from urllib.parse import unquote

    if '#' not in link:
        return ''
    try:
        return unquote(link.rsplit('#', 1)[-1]).strip()
    except Exception:
        return link.rsplit('#', 1)[-1].strip()


def link_host(link: str) -> str:
    body = link.split('://', 1)[-1].split('#', 1)[0].split('?', 1)[0]
    host = body.rsplit('@', 1)[-1]
    return host.rsplit(':', 1)[0].strip('[]').lower()


def is_banner_link(link: str) -> bool:
    """Ссылка-подпись, а не сервер.

    Два признака, и хватает любого: значок-стрелка в названии и адрес,
    которого не бывает, — в подписях туда пишут что попало вроде `11111`.
    """
    remark = link_remark(link)
    if any(low <= ord(char) <= high
           for char in remark for low, high in BANNER_RANGES):
        return True

    host = link_host(link)
    if not host:
        return True
    # доменное имя или IP; всё остальное — заглушка
    return '.' not in host and ':' not in host


# Как в записи расхода по ноде называют пользователя и объём
USER_KEYS = ('userUuid', 'uuid', 'user_uuid')
NAME_KEYS = ('username', 'userName', 'name')
TOTAL_KEYS = ('total', 'totalBytes', 'usedBytes', 'usedTrafficBytes', 'bytes')


def describe(servers: list[dict]) -> str:
    """Чем занята машина — строкой на каждого соседа.

    Сквад в сообщении есть, а что за ним стоит — нет, и выяснять это
    приходилось перебором /srvdiag по всем серверам подряд.
    """
    rows = []
    for server in servers:
        plan = ps.plan_of(server)
        rows.append(f'{server["_id"]} — {plan.title if plan else server.get("plan")}, '
                    f'{ps.location_title(server)}, '
                    f'владелец {server.get("owner_id")}, '
                    f'{ps.STATUS_TITLES.get(server.get("status"), server.get("status"))}')
    return '\n'.join(rows)


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ''
    server: dict | None = None
    amount: int = 0
    # Подробности отказа для человека: код ошибки говорит, что не так, а
    # note — с чем именно. Без него «на машине другой тариф» не отвечает на
    # единственный нужный вопрос: какой сервер её занял.
    note: str = ''


class PrivateServerService:
    def __init__(self, users, servers, settings, vpn, notifier=None, bot=None,
                 pool=None):
        self.users = users
        self.servers = servers
        self.settings = settings
        self.vpn = vpn
        self.notifier = notifier
        self.bot = bot
        self.pool = pool

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
        # Кнопка с лимитной площадкой для доли не рисуется, но открытый
        # раньше экран живёт в чате вечно, и нажать его можно и завтра.
        if not ps.allowed_location(plan, place):
            return Result(False, 'limited_location')
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

    async def shares_limit(self, plan: ps.ServerPlan) -> int:
        """Сколько долей сажаем на машину. Правится из админки, как и цены."""
        if not plan.shared:
            return 1
        return await self.settings.int('private.shares') or plan.shares

    async def free_squads(self, location: str, profile: str) -> list[dict]:
        """Машины, куда можно подсадить ещё одну долю.

        Админу иначе пришлось бы держать в голове, какой сквад чем занят:
        доли выдаются по одной, а сервер под ними один и тот же.
        """
        live = await self.servers.col.find(
            {'status': {'$in': list(ps.LIVE_STATUSES)},
             'squad_uuid': {'$nin': ['', None]}}).to_list(length=300)

        by_squad: dict[str, list[dict]] = {}
        for server in live:
            by_squad.setdefault(server['squad_uuid'], []).append(server)

        rows = []
        for squad, servers in by_squad.items():
            plan = ps.plan_of(servers[0])
            if not plan or not plan.shared:
                continue
            # Локация и протокол у соседей по машине общие — подсадить долю
            # в другую страну физически нельзя.
            if servers[0].get('location') != location or servers[0].get('profile') != profile:
                continue
            limit = await self.shares_limit(plan)
            if len(servers) < limit:
                rows.append({'squad': squad, 'used': len(servers), 'limit': limit,
                             'title': servers[0].get('title') or '',
                             'location': servers[0].get('location') or '',
                             'profile': servers[0].get('profile') or ''})
        return rows

    # ── запас поднятых машин ────────────────────────────────────────────────
    #
    # Провижининг ручной, и заявка ждёт человека. Машины, поднятые заранее,
    # снимают это ожидание: заявка садится на готовый сквад в тот же миг.

    async def pool_add(self, squad_uuid: str, location: str, profile: str,
                       admin_id: int = 0, note: str = '') -> Result:
        if self.pool is None:
            return Result(False, 'no_pool')
        if location not in ps.BY_LOCATION:
            return Result(False, 'unknown_location')
        if profile not in ps.BY_PROFILE:
            return Result(False, 'unknown_profile')
        if await self.servers.on_squad(squad_uuid):
            # Сквад уже под живым сервером: в запасе ему делать нечего.
            return Result(False, 'squad_busy', note=describe(
                await self.servers.on_squad(squad_uuid)))
        if not await self.pool.add(squad_uuid, location, profile, admin_id, note):
            return Result(False, 'already_in_pool')

        log.info('в запас добавлена машина %s (%s, %s)', squad_uuid, location, profile)
        return Result(True)

    async def pool_remove(self, squad_uuid: str) -> Result:
        if self.pool is None:
            return Result(False, 'no_pool')
        return Result(await self.pool.remove(squad_uuid), 'not_found')

    async def pool_rows(self) -> list[dict]:
        """Запас с пометкой, кто на какой машине уже сидит.

        Свободна машина или нет, считается по серверам, а не по полю в
        записи: два источника правды однажды разойдутся, и выдастся занятое.
        """
        if self.pool is None:
            return []

        rows = []
        for entry in await self.pool.all():
            live = await self.servers.on_squad(entry['squad_uuid'])
            plan = ps.plan_of(live[0]) if live else None
            limit = await self.shares_limit(plan) if plan else 0
            rows.append({**entry, 'used': len(live), 'limit': limit or 1,
                         'free': (len(live) < limit) if limit else not live,
                         'servers': live})
        return rows

    async def pick_squad(self, server: dict) -> str:
        """Готовая машина под эту заявку. Пусто — поднимать руками.

        Сначала досаживаем к своим же долям: место там уже оплачено железом,
        и тратить на заявку целую машину незачем.
        """
        plan = ps.plan_of(server)
        if not plan:
            return ''

        location = server.get('location') or ''
        profile = server.get('profile') or ''

        if plan.shared:
            for row in await self.free_squads(location, profile):
                return row['squad']

        if self.pool is None:
            return ''
        for entry in await self.pool.all():
            if entry.get('location') != location or entry.get('profile') != profile:
                continue
            if await self.servers.on_squad(entry['squad_uuid']):
                continue          # машину уже разобрали
            return entry['squad_uuid']
        return ''

    async def give_from_pool(self, server_id: str) -> Result:
        """Выдать заявке готовую машину. Ошибка — если готовой нет."""
        server = await self.servers.get(server_id)
        if not server:
            return Result(False, 'not_found')
        if server.get('status') != ps.REQUESTED:
            return Result(False, 'wrong_status', server=server)

        squad = await self.pick_squad(server)
        if not squad:
            return Result(False, 'no_free_machine', server=server)

        result = await self.activate(server_id, squad)
        if result.ok:
            await self.tell_ready(result.server)
        return result

    async def tell_ready(self, server: dict) -> None:
        """Сказать владельцу, что сервер работает. Текст один на все пути:
        и на ручную выдачу, и на выдачу из запаса."""
        await self._tell(
            server['owner_id'],
            f'{e("private")} <b>Ваш сервер готов</b>\n\n'
            f'«{server.get("title")}» уже работает.\n'
            f'Локация: {ps.location_title(server)}, '
            f'протокол: {ps.profile_title(server)}.\n'
            f'Мест: {server.get("slots")}, оплачен до '
            f'{fmt(server.get("paid_until"))}.\n\n'
            + (f'Эту же машину купили {ps.others_on_machine(ps.shares_of(server))}, '
               f'но места и статистика у вас свои: их и их гостей вы не видите, '
               f'они вас тоже.\n\n' if ps.is_shared(server) else '')
            + 'Откройте профиль → «Свой сервер», чтобы позвать друзей.')

    async def queue(self) -> list[dict]:
        """Что осталось поднять: заявки, сгруппированные по «машине».

        Считаем не заявки, а машины: три доли в Токио — это один VPS, а
        список из трёх строк заставляет поднять три.
        """
        pending = await self.servers.pending()
        groups: dict[tuple, dict] = {}
        for server in pending:
            plan = ps.plan_of(server)
            key = (server.get('plan'), server.get('location'), server.get('profile'))
            group = groups.setdefault(key, {
                'plan': plan, 'plan_code': server.get('plan'),
                'location': ps.BY_LOCATION.get(server.get('location') or ''),
                'profile': ps.BY_PROFILE.get(server.get('profile') or ''),
                'servers': [], 'ready': 0})
            group['servers'].append(server)

        for group in groups.values():
            per_machine = (await self.shares_limit(group['plan'])
                           if group['plan'] else 1)

            # Сколько заявок закроет имеющееся железо. Считаем с местами:
            # первая заявка занимает машину целиком, а первая доля — только
            # одно место из трёх, и остальные две ещё поместятся.
            ready = 0
            rooms: dict[str, int] = {}
            for server in group['servers']:
                squad = await self.pick_squad(server)
                if not squad:
                    continue
                if squad not in rooms:
                    live = len(await self.servers.on_squad(squad))
                    rooms[squad] = max(0, per_machine - live)
                if rooms[squad] <= 0:
                    continue
                rooms[squad] -= 1
                ready += 1

            group['ready'] = ready
            group['count'] = len(group['servers'])
            # сколько машин надо поднять: доли делят одну, остальные — по одной
            left = max(0, group['count'] - group['ready'])
            group['machines'] = -(-left // max(1, per_machine))

        return sorted(groups.values(), key=lambda g: -g['count'])

    async def activate(self, server_id: str, squad_uuid: str) -> Result:
        """Админ поднял VPS и привязал сквад — сервер начинает работать."""
        server = await self.servers.get(server_id)
        if not server:
            return Result(False, 'not_found')
        if server.get('status') != ps.REQUESTED:
            return Result(False, 'wrong_status', server=server)

        # Один сквад — одна машина. Сколько долей на неё можно посадить,
        # решает тариф: обычный сервер занимает её целиком, и повторная
        # выдача того же сквада почти всегда опечатка в UUID.
        plan = ps.plan_of(server)
        neighbours = await self.servers.on_squad(squad_uuid)
        limit = await self.shares_limit(plan) if plan else 1
        if len(neighbours) >= limit:
            return Result(False, 'squad_full' if limit > 1 else 'squad_busy',
                          server=server, note=describe(neighbours))
        # Смешивать тарифы на одной машине нельзя: у соседей общие локация,
        # протокол и квота трафика, а мест продано было бы разное.
        if neighbours and any(n.get('plan') != server.get('plan')
                              or n.get('location') != server.get('location')
                              for n in neighbours):
            return Result(False, 'squad_mismatch', server=server,
                          note=describe(neighbours))

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

    async def wipe(self, server_id: str, refund: bool = False) -> Result:
        """Стереть сервер совсем: доступ снять, документ удалить.

        Отличается от `close` тем, что не оставляет следа: закрытый сервер
        виден в истории и в выгрузках, а этот нужен, чтобы пройти покупку
        заново на своём же аккаунте. Деньги по умолчанию не возвращаются —
        это команда для тестов, а не отмена заказа.
        """
        server = await self.servers.get(server_id)
        if not server:
            return Result(False, 'not_found')

        for user_id in self._everyone(server):
            try:
                await self._revoke(server, user_id)
            except Exception:
                # Панель могла и не знать про этот сквад. Документ всё равно
                # убираем: иначе человек остаётся с сервером, которого нет.
                log.exception('доступ %s к серверу %s не снят при удалении',
                              user_id, server_id)

        amount = int(server.get('price') or 0) if refund else 0
        if amount:
            await self.users.credit(server['owner_id'], amount,
                                    'Возврат за личный сервер')

        await self.servers.delete(server_id)
        log.warning('сервер %s удалён совсем (владелец %s, возврат %s₽)',
                    server_id, server.get('owner_id'), amount)
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
    async def _server_usage(self, server: dict) -> tuple[dict, str, bool]:
        """Расход участников на этом сервере: (расход, заметка, ответила ли панель).

        Третье значение отделяет «панель сказала ноль» от «панель не
        сказала ничего». Ноль — законный ответ на свежем сервере, и
        подменять его общим трафиком человека нельзя: 2 ТБ вместо нуля
        пугают сильнее, чем честный ноль.

        Ключи — и числовой id из панели, и username: каким совпадёт,
        зависит от того, какая ручка ответила.
        """
        squad = server.get('squad_uuid')
        if not squad:
            return {}, 'у сервера не задан сквад', False

        # Окно — последние 30 дней. Не «с даты активации»: нода могла
        # работать и до привязки к боту, и на свежем сервере такой период
        # схлопывается в один день, где расхода ещё нет. Тридцать дней
        # покрывают и текущий оплаченный месяц целиком.
        until = now() + timedelta(days=1)
        since = now() - timedelta(days=ps.CHARGE_PERIOD_DAYS)

        try:
            by_squad = await self.vpn.squad_usage(squad, since, until)
            if by_squad:
                return by_squad, '', True
        except Exception as exc:
            log.warning('расход сквада %s не получен: %s', squad, exc)

        try:
            nodes = await self.vpn.squad_nodes(squad)
        except Exception as exc:
            log.warning('ноды сквада %s не получены: %s', squad, exc)
            return {}, f'ноды сквада: {exc}', False
        if not nodes:
            return {}, 'в скваде нет нод', False

        usage: dict = {}
        note = ''
        answered = False
        for node in nodes:
            node_uuid = node.get('uuid') or node.get('nodeUuid')
            if not node_uuid:
                continue
            try:
                for name, total in (await self.vpn.node_users_usage(
                        node_uuid, since, until)).items():
                    usage[name] = usage.get(name, 0) + total
                answered = True
            except Exception as exc:
                note = f'расход ноды: {exc}'
                log.warning('расход ноды %s не получен: %s', node_uuid, exc)

        if not answered and not note:
            note = f'ноды сквада ({len(nodes)}) без опознаваемого uuid'
        return usage, note, answered

    async def usage_probe(self, server: dict) -> list[str]:
        """Что панель отвечает на ручки расхода. Только для диагностики.

        Пересказ ответа своими словами уже трижды оказывался неточным,
        поэтому здесь ответ показывается как есть.
        """
        squad = server.get('squad_uuid')
        if not squad:
            return ['сквад не задан']

        until = now() + timedelta(days=1)
        since = now() - timedelta(days=ps.CHARGE_PERIOD_DAYS)

        out = [f'период: {since:%Y-%m-%d} … {until:%Y-%m-%d}']
        try:
            nodes = await self.vpn.squad_nodes(squad)
            out.append(f'нод в скваде: {len(nodes)}')
            out.append(f'ноды: {str(nodes)[:400]}')
        except Exception as exc:
            out.append(f'accessible-nodes: {exc}')
            return out

        for node in nodes[:3]:
            node_uuid = node.get('uuid') or node.get('nodeUuid')
            if not node_uuid:
                continue
            try:
                raw = await self.vpn.node_users_raw(node_uuid, since, until)
                out.append(f'расход {node_uuid}: {str(raw)[:500]}')
            except Exception as exc:
                out.append(f'расход {node_uuid}: {exc}')
        return out

    async def stats(self, server: dict) -> list[dict]:
        """Трафик и последнее подключение по каждому участнику.

        Панель опрашивается по одному человеку: участников максимум
        пятнадцать, а отдельного пакетного метода в API нет.
        """
        rows = []
        by_server, usage_note, answered = await self._server_usage(server)
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
            # Панель ответила, а человека в списке нет — значит он на этом
            # сервере ничего не потратил. Это ноль, а не отсутствие данных.
            if scoped is None and answered:
                scoped = 0
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

    async def router_links(self, server: dict, user_id: int) -> tuple[list[str], str]:
        """Ссылка для роутера: (ссылки, заметка).

        Роутеру нужна прямая ссылка, а не подписка: прошивки умеют xray, но
        не умеют её обновлять. Отдаём ровно одну — ту, что ведёт на сервер
        владельца, а не первую попавшуюся из подписки.
        """
        if not ps.router_ready(server):
            return [], 'на роутер ставится только TCP Reality и gRPC'

        user = await self.users.get(user_id, {'vpn.uuid': 1})
        uuid = self.users.pick(user or {}, 'vpn.uuid')
        if not uuid:
            return [], 'подписки в панели нет'

        try:
            keys = await self.vpn.connection_keys(uuid, user_id)
        except Exception as exc:
            log.warning('ссылки подключения %s не получены: %s', user_id, exc)
            return [], str(exc)

        vless = [key for key in keys if key.lower().startswith('vless://')]
        if not vless:
            return [], 'панель не отдала ни одной VLESS-ссылки'

        # Имя ноды сервера — самый надёжный признак: если панель его отдала,
        # гадать не нужно.
        names = []
        try:
            for node in await self.vpn.squad_nodes(server.get('squad_uuid') or ''):
                name = node.get('nodeName') or node.get('name')
                if name:
                    names.append(str(name).lower())
        except Exception as exc:
            log.info('имя ноды сервера не получено: %s', exc)

        def mine(link: str) -> bool:
            tail = link_remark(link).lower()
            return bool(tail) and any(name in tail or tail in name for name in names)

        ours = [link for link in vless if mine(link)]
        if ours:
            return ours[-1:], ''

        # По имени не вышло. Служебные строки-подписи («⬆️Обход LTE в
        # описании подписки⬆️») лежат в подписке такими же ссылками, и
        # роутеру они не годятся — отсеиваем их и берём последнюю рабочую:
        # личный сервер добавляется после общих и идёт в конце списка.
        real = [link for link in vless if not is_banner_link(link)] or vless
        return real[-1:], (f'по подписи «{link_remark(real[-1]) or "без названия"}». '
                           f'Если это не ваш сервер — напишите в поддержку')

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
            # На долевой машине живут ещё двое. Погасить VPS по такому
            # сообщению — значит выключить сервер оплатившим людям.
            left = len(await self.servers.on_squad(server.get('squad_uuid') or ''))
            what = (f'освободилась доля, на машине осталось {left} — VPS не гасить'
                    if left else 'можно гасить VPS')
            await self.notifier.send(
                'payments', f'Сервер {server["_id"]} закрыт ({reason}) — {what}')

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

        fresh_subscription = not vpn.get('uuid')
        if fresh_subscription:
            created = await self.vpn.create_subscription(
                user_id, days=max(1, (paid_until - now()).days))
            await self.users.set_vpn(user_id, {
                'uuid': created['uuid'], 'shortUuid': created['shortUuid'],
                'expireAt': created['expireAt'], 'createdAt': created['createdAt'],
                'period': vpn.get('period') or 1,
                # Подписки у человека не было вовсе. Запоминаем это моментом
                # «сейчас»: при выходе срок вернётся сюда, то есть истечёт.
                # Иначе гость, зашедший на день, уносит с собой рабочую
                # подписку до конца оплаченного владельцем месяца.
                'private_prev_expire': now(),
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
            if not vpn.get('private_prev_expire') and not fresh_subscription:
                fields['private_prev_expire'] = expires
            fields['expireAt'] = paid_until

        await self.vpn.update_subscription(
            vpn['uuid'], squads=squads, status='ACTIVE',
            expire_at=paid_until if expires and expires < paid_until else None)
        await self.users.set_vpn(user_id, fields)

        # Доступ к серверу двигает срок основной подписки — ByPass должен
        # ехать вместе с ней, иначе он кончается посреди оплаченного месяца.
        if 'expireAt' in fields:
            await bypass.sync_expiry(self.users, self.vpn, user_id, fields['expireAt'],
                                     vpn=vpn)

    async def _revoke(self, server: dict, user_id: int) -> None:
        """Снять доступ и вернуть человеку его собственный срок."""
        squad = server.get('squad_uuid')
        user = await self.users.get(user_id)
        vpn = (user or {}).get('vpn') or {}
        if not vpn.get('uuid'):
            return

        # На долевой машине сквад общий. Если человек сидит ещё и в соседней
        # доле — забирать сквад нельзя: он потеряет доступ там, где его
        # никто не выгонял.
        if await self._elsewhere_on_squad(server, user_id):
            log.info('%s остаётся на скваде %s по другой доле', user_id, squad)
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

        # Срок вернулся к своему — ByPass возвращаем туда же: иначе он
        # остаётся действовать дольше подписки, за которую никто не платил.
        if previous:
            await bypass.sync_expiry(self.users, self.vpn, user_id, previous, vpn=vpn)

    async def _elsewhere_on_squad(self, server: dict, user_id: int) -> bool:
        """Есть ли у человека доступ к этой же машине по другой доле."""
        squad = server.get('squad_uuid')
        if not squad or not ps.is_shared(server):
            return False
        return any(other['_id'] != server['_id'] and ps.is_member(other, user_id)
                   for other in await self.servers.on_squad(squad))

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
