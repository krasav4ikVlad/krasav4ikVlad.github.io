"""Единственное место, которое ходит в панель Remnawave по HTTP.

Собрано из шести функций utils.py: create_vpn_subscription,
create_bypass_vpn_subscription, update_user_subscription, fetch_hwid_devices,
remna_delete_hwid, renew_user_subscription_with_required_squads.

Что изменилось по сравнению с оригиналом:

* адрес панели и токен приходят из конфига, а не строкой в шести местах;
* один общий http-клиент вместо `async with httpx.AsyncClient()` на каждый
  вызов — соединения переиспользуются, а не открываются заново;
* лимит устройств новой подписки берётся из настроек (было зашито 2);
* ошибка панели — исключение VpnPanelError, а не {'status': False}, который
  половина вызывающего кода не проверяла;
* PATCH остаётся частичным: передаются только заданные поля, поэтому продление
  не обнуляет trafficLimit и hwidDeviceLimit (это то, о чём предупреждает
  комментарий в lifeline.py).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timedelta

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt

log = logging.getLogger(__name__)


def _rows(data) -> list[dict]:
    """Список записей из ответа любой формы: сам список или список внутри.

    Внутри может лежать несколько списков (categories, sparklineData,
    topUsers) — берём тот, где словари, а не строки и числа.
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    for value in (data or {}).values():
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                return rows
    return []


# Чем в записи расхода назван пользователь и чем — объём. Набор разный у
# разных версий панели, поэтому раскладываем запись по всем именам сразу:
# совпасть потом можно любым.
IDENTITY_KEYS = ('username', 'userUuid', 'uuid', 'id', 'userId')
AMOUNT_KEYS = ('total', 'totalBytes', 'usedBytes', 'usedTrafficBytes', 'bytes')


def usage_by_identifier(data) -> dict:
    """Ответ ручки расхода → {любой идентификатор пользователя: байты}."""
    usage: dict = {}
    for row in _rows(data):
        amount = next((row[key] for key in AMOUNT_KEYS
                       if isinstance(row.get(key), (int, float))), None)
        if amount is None:
            continue
        for key in IDENTITY_KEYS:
            value = row.get(key)
            if value in (None, ''):
                continue
            usage[str(value)] = usage.get(str(value), 0) + int(amount)
            if isinstance(value, (int, float)):
                usage[int(value)] = usage.get(int(value), 0) + int(amount)
    return usage


# Панель отвечает на повтор по-разному в зависимости от версии: где-то 409,
# где-то 400 с текстом про username или short UUID. Общее у всех ответов —
# «already exists», по нему и отличаем «такой уже есть» от настоящего отказа.
def _already_exists(exc: Exception) -> bool:
    text = str(exc).lower()
    return 'already exists' in text or 'http 409' in text


# ── чем панель опознаёт пользователя ────────────────────────────────────────
#
# До 3.0 это был uuid, начиная с 3.0 — числовой id, а само поле uuid из
# ответов убрано. Меняются при этом только значение и имя поля в теле
# запроса: пути остались той же формы, /api/users/<чем-опознаём>.
#
# Поэтому весь бот продолжает хранить «ссылку на пользователя панели» в
# vpn.uuid и передавать её сюда, а клиент по виду значения понимает, о какой
# панели идёт речь. Так работают обе версии сразу — это не роскошь: в момент
# обновления панели старые подписки ещё с uuid, новые уже с id, и жить с
# перемешанной базой придётся в любом случае.


def is_numeric_ref(ref) -> bool:
    return isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit())


def ref_field(ref) -> str:
    """Имя поля в теле запроса: id для панели 3.x, uuid для прежних."""
    return 'id' if is_numeric_ref(ref) else 'uuid'


def user_ref(user: dict) -> str | int | None:
    """Ссылка на пользователя из ответа панели — какую бы версию ни отвечала."""
    if not isinstance(user, dict):
        return None
    value = user.get('uuid')
    return value if value else user.get('id')


def _with_ref(user: dict) -> dict:
    """Проставить uuid ответу панели 3.x.

    Вызывающий код всех сервисов читает из ответа `uuid` и кладёт его в
    vpn.uuid. Панель 3.x этого поля не отдаёт вовсе, и без подстановки
    подписка сохранилась бы без ссылки на панель — то есть выданной, но
    неуправляемой: ни продлить, ни отключить, ни показать устройства.
    """
    if isinstance(user, dict) and not user.get('uuid') and user.get('id') is not None:
        return dict(user, uuid=user['id'])
    return user


def _user_record(data) -> dict:
    """Пользователь из ответа: сам объект, объект в обёртке или первый в списке."""
    if isinstance(data, dict) and user_ref(data):
        return _with_ref(data)
    for row in _rows(data):
        if user_ref(row):
            return _with_ref(row)
    return {}


def _links(data) -> list[str]:
    """Ссылки из ответа: включённые ключи, а скрытые и выключенные — мимо.

    Часть версий отдаёт их в base64, часть — как есть. Отличаем по схеме:
    рабочая ссылка начинается с протокола.
    """
    import base64

    raw = []
    if isinstance(data, dict):
        raw = data.get('enabledKeys') or []
    elif isinstance(data, list):
        raw = data

    links = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        value = item.strip()
        if '://' not in value:
            try:
                value = base64.b64decode(value + '=' * (-len(value) % 4)).decode()
            except Exception:
                continue
        links.extend(part for part in value.splitlines() if '://' in part)
    return links


def subscription_token(user_id: int, bypass: bool = False) -> str:
    """Короткий идентификатор подписки. Детерминированный: тот же вход — тот же токен."""
    raw = f'rsvpn-bypass-{user_id}-vpn-core' if bypass else f'rsvpn-{user_id}-vpn-core'
    digest = hashlib.sha1(raw.encode()).digest()[:10]
    return base64.b32encode(digest).decode().rstrip('=').lower()


class RemnawaveClient:
    def __init__(self, base_url: str, token: str, http, settings=None, squads=None,
                 dry_run: bool = False):
        self._base = (base_url or '').rstrip('/')
        self._token = token
        self._http = http
        self._settings = settings
        self._squads = squads
        # dry_run: запросы на изменение не уходят в панель, только пишутся в лог.
        # Нужен, когда тестовый бот работает на копии боевых данных: подписки
        # в панели настоящие, и продление «понарошку» изменило бы их всерьёз.
        self._dry_run = dry_run
        # Ручка расхода по скваду есть не во всех версиях панели: на 404
        # запоминаем это и больше не ходим — иначе каждый показ статистики
        # начинается с заведомо неудачного запроса.
        self._squad_usage_supported: bool | None = None
        if dry_run:
            log.warning('панель в режиме только чтения: изменения не отправляются')

    # ── низкий уровень ──────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if self._dry_run and method.upper() != 'GET':
            payload = kwargs.get('json') or {}
            log.info('[dry-run] %s %s %s', method, path, payload)
            return {'uuid': payload.get('uuid', 'dry-run'),
                    'shortUuid': payload.get('shortUuid', 'dry-run'),
                    'expireAt': payload.get('expireAt'),
                    'createdAt': payload.get('createdAt'),
                    'dry_run': True}

        url = f'{self._base}{path}'
        try:
            response = await self._http.request(
                method, url,
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {self._token}'},
                **kwargs,
            )
        except Exception as exc:
            raise VpnPanelError(f'{method} {path}: {exc}') from exc

        # 201 отдаёт создание, 202 — фоновые операции, 204 — удаление.
        # Панель 3.x перешла на них с прежних «всегда 200», и без этого списка
        # успешное удаление читалось бы как отказ.
        if response.status_code not in (200, 201, 202, 204):
            text = getattr(response, 'text', '')
            raise VpnPanelError(f'{method} {path}: HTTP {response.status_code} {text[:300]}')

        # У 202 и 204 тела нет по определению — разбирать нечего.
        if response.status_code in (202, 204):
            return {}

        try:
            payload = response.json()
        except Exception as exc:
            raise VpnPanelError(f'{method} {path}: ответ не JSON') from exc

        return payload.get('response', payload) if isinstance(payload, dict) else payload

    # ── подписки ────────────────────────────────────────────────────────────
    async def _find_user(self, username: str, short_uuid: str) -> dict:
        """Найти пользователя панели по имени или короткому идентификатору.

        Имя ручки в разных версиях панели своё, поэтому пробуем обе и
        считаем 404 нормальным ответом: «не нашли по этой — ищем по той».
        """
        paths = []
        if username:
            paths.append(f'/api/users/by-username/{username}')
        if short_uuid:
            paths.append(f'/api/users/by-short-uuid/{short_uuid}')

        for path in paths:
            try:
                found = _user_record(await self._request('GET', path))
            except VpnPanelError as exc:
                if 'HTTP 404' in str(exc):
                    continue
                log.warning('поиск в панели по %s не удался: %s', path, exc)
                continue
            if found:
                return found
        return {}

    async def find_by_short_uuid(self, short_uuid: str) -> dict:
        """Пользователь панели по короткому идентификатору.

        Единственный поиск, переживший переход на 3.x, и потому единственный
        способ узнать новый числовой id для подписки, заведённой раньше.
        """
        return await self._find_user('', short_uuid)

    async def _create_or_adopt(self, payload: dict, keep_longer: bool = False) -> dict:
        """POST /api/users, а если такой пользователь в панели уже есть — взять его.

        Ровно этот случай ломался чаще всего. Запись в панели создаётся, а
        ответ до бота не доходит: оборванное соединение, таймаут, перезапуск.
        Деньги бот возвращает, подписку у себя не сохраняет — и человек
        остаётся с записью в панели, о которой бот не знает. Дальше каждая
        следующая попытка упиралась в «short UUID already exists»: shortUuid
        считается от user_id и всегда совпадает с прежним. Выбраться из этого
        сам человек не мог — покупка не проходила уже никогда.

        Поэтому повтор здесь не отказ, а продолжение прерванной выдачи: берём
        существующую запись и доводим её до того вида, в котором она должна
        быть после покупки.
        """
        try:
            return _with_ref(await self._request('POST', '/api/users', json=payload))
        except VpnPanelError as exc:
            if not _already_exists(exc):
                raise
            found = await self._find_user(payload.get('username', ''),
                                          payload.get('shortUuid', ''))
            if not user_ref(found):
                raise

        expire_at = parse_dt(payload.get('expireAt'))
        if keep_longer:
            # Срок не укорачиваем и не удваиваем: если у записи уже больше,
            # оставляем её. Второй раз оплаченные дни не добавляются — иначе
            # прерванная выдача давала бы двойной срок.
            have = parse_dt(found.get('expireAt'))
            if have and expire_at and have > expire_at:
                expire_at = have

        updated = await self.update_subscription(
            user_ref(found),
            expire_at=expire_at,
            device_limit=payload.get('hwidDeviceLimit'),
            traffic_bytes=payload.get('trafficLimitBytes'),
            squads=payload.get('activeInternalSquads') or None,
            status='ACTIVE',
        )
        log.warning('подписка %s уже была в панели — продолжили прерванную выдачу',
                    payload.get('username'))
        # Ответ PATCH беднее ответа POST: подставляем найденную запись снизу,
        # чтобы вызывающий код получил тот же набор полей, что и при создании.
        return _with_ref({**found, **(updated or {})})

    async def create_subscription(self, user_id: int, days: int) -> dict:
        started = now()
        device_limit = await self._setting_int('price.default_device_limit', 2)
        squads = await self._squads.for_new_user() if self._squads else []

        return await self._create_or_adopt({
            'username': str(user_id),
            'status': 'ACTIVE',
            'shortUuid': subscription_token(user_id),
            'expireAt': (started + timedelta(days=days)).isoformat(),
            'createdAt': started.isoformat(),
            'telegramId': user_id,
            'hwidDeviceLimit': device_limit,
            'activeInternalSquads': squads,
        }, keep_longer=True)

    async def create_bypass_subscription(self, user_id: int, expire_at: datetime,
                                         traffic_bytes: int | None = None) -> dict:
        default_gb = await self._setting_int('bypass.default_traffic_gb', 1)
        squad = await self._setting_str('bypass.squad_uuid')
        external = await self._setting_str('bypass.external_squad_uuid')

        payload = {
            'username': f'{user_id}_bypass',
            'status': 'ACTIVE',
            'shortUuid': subscription_token(user_id, bypass=True),
            'expireAt': expire_at.isoformat(),
            'createdAt': now().isoformat(),
            'telegramId': user_id,
            'hwidDeviceLimit': await self._setting_int('price.default_device_limit', 2),
            'trafficLimitBytes': traffic_bytes or default_gb * 1024 ** 3,
            'trafficLimitStrategy': 'NO_RESET',
        }
        if squad:
            payload['activeInternalSquads'] = [squad]
        if external:
            payload['externalSquadUuid'] = external

        # Срок здесь равен сроку основной подписки, поэтому у найденной записи
        # он не сохраняется, а выравнивается: расхождение дат — отдельная беда,
        # которую чинит app.services.bypass.
        return await self._create_or_adopt(payload)

    async def update_subscription(self, ref: str | int, *, expire_at: datetime | str | None = None,
                                  traffic_bytes: int | None = None,
                                  device_limit: int | None = None,
                                  squads: list[str] | None = None,
                                  status: str | None = None) -> dict:
        """Частичный PATCH: передаются только заданные поля.

        Кого править, панель 3.x читает из поля `id`, прежние — из `uuid`.
        Ставим то, которое соответствует виду ссылки: лишнее поле панель 3.x
        отвергает проверкой схемы, а не игнорирует.
        """
        payload: dict = {ref_field(ref): ref}

        if status is not None:
            payload['status'] = status

        if expire_at is not None:
            payload['expireAt'] = (expire_at.isoformat()
                                   if isinstance(expire_at, datetime) else expire_at)
        if traffic_bytes is not None:
            payload['trafficLimitBytes'] = traffic_bytes
        if device_limit is not None:
            payload['hwidDeviceLimit'] = device_limit
        if squads is not None:
            payload['activeInternalSquads'] = squads

        return _with_ref(await self._request('PATCH', '/api/users', json=payload))

    async def renew_subscription(self, user: dict, expire_at: datetime) -> tuple[dict, list[str]]:
        """Продление с сохранением набора сквадов.

        В оригинале userid искался по полям info.telegram_id / telegramId /
        userid / _id — ни одного из них в документе нет, поэтому в качестве
        userid уезжал ObjectId. Здесь берётся user_data.user_id.
        """
        vpn = user.get('vpn') or {}
        uuid = vpn.get('uuid')
        if not uuid:
            raise VpnPanelError('у пользователя нет vpn.uuid')

        squads = (await self._squads.for_existing_user(vpn.get('activeInternalSquads'))
                  if self._squads else None)
        data = await self.update_subscription(uuid, expire_at=expire_at, squads=squads)
        return data, squads or []

    # ── устройства ──────────────────────────────────────────────────────────
    async def set_status(self, ref: str | int, status: str) -> dict:
        """ACTIVE / DISABLED — включить или отключить подписку в панели.

        Нужно жёсткой блокировке: обычный бан только закрывает бота, а
        конфиг у человека продолжает работать, пока не истечёт срок.
        """
        return await self.update_subscription(ref, status=status)

    async def get_subscription(self, ref: str | int) -> dict:
        """Пользователь панели целиком: трафик, статус, когда был онлайн.

        Нужен владельцу личного сервера: без этого «статистика сервера» — это
        список имён без единой цифры.
        """
        if not ref:
            return {}
        return _with_ref(await self._request('GET', f'/api/users/{ref}') or {})

    async def squad_usage(self, squad_uuid: str, start: datetime, end: datetime,
                          page_limit: int = 500) -> dict[int, int]:
        """Расход каждого участника сквада за период: {id пользователя: байты}.

        Личный сервер — это внутренний сквад, поэтому здесь и есть ответ на
        «кто ест мой сервер». Общий userTraffic из карточки для этого не
        годится: он считает весь трафик человека по всем нодам.

        Даты — именно даты (YYYY-MM-DD), так объявлено в спеке панели:
        `format: date`. С полным ISO-временем ручка отвечает отказом.
        """
        if not squad_uuid or self._squad_usage_supported is False:
            return {}

        usage: dict[int, int] = {}
        cursor = None
        for _ in range(20):                 # предохранитель от бесконечной страницы
            params = {'start': start.strftime('%Y-%m-%d'),
                      'end': end.strftime('%Y-%m-%d'),
                      'limit': page_limit}
            if cursor is not None:
                params['cursor'] = cursor

            try:
                data = await self._request(
                    'GET', f'/api/bandwidth-stats/internal-squads/{squad_uuid}/usage',
                    params=params)
            except VpnPanelError as exc:
                if 'HTTP 404' in str(exc):
                    log.info('панель без ручки расхода по скваду — считаем по нодам')
                    self._squad_usage_supported = False
                    return {}
                raise
            self._squad_usage_supported = True

            for row in (data or {}).get('users') or []:
                if not isinstance(row, dict):
                    continue
                total = int(row.get('totalBytes') or 0)
                # Раскладываем и по id, и по username. Что означает `id`,
                # у разных версий панели своё — с 3.0 это идентификатор
                # пользователя панели, а не telegram id; username остаётся
                # telegram id строкой при любой версии.
                if row.get('id') is not None:
                    usage[int(row['id'])] = total
                if row.get('username'):
                    usage[str(row['username'])] = total

            if not (data or {}).get('hasMore'):
                break
            cursor = (data or {}).get('nextCursor')
            if cursor is None:
                break
        return usage

    async def squad_nodes(self, squad_uuid: str) -> list[dict]:
        """Ноды, доступные внутреннему скваду. Личный сервер — это одна нода."""
        if not squad_uuid:
            return []
        data = await self._request(
            'GET', f'/api/internal-squads/{squad_uuid}/accessible-nodes')
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        nodes: list[dict] = []
        for value in (data or {}).values():
            if isinstance(value, list):
                nodes.extend(item for item in value if isinstance(item, dict))
        return nodes

    async def squad(self, squad_uuid: str) -> dict:
        """Сам сквад: имя и его инбаунды. По ним видно транспорт машины."""
        if not squad_uuid:
            return {}
        data = await self._request('GET', f'/api/internal-squads/{squad_uuid}')
        return data if isinstance(data, dict) else {}

    async def node_users_usage(self, node_uuid: str, start: datetime, end: datetime,
                               top: int = 200) -> dict[str, int]:
        """Расход по нодам для панелей без ручки сквада: {username: байты}.

        Ответ — topUsers с полями color/username/total, поэтому совпадение
        идёт по username; у нас это telegram id строкой.
        """
        if not node_uuid:
            return {}
        data = await self.node_users_raw(node_uuid, start, end, top)
        return usage_by_identifier(data)

    async def node_users_raw(self, node_uuid: str, start: datetime, end: datetime,
                             top: int = 200):
        """Сырой ответ ручки расхода по ноде — им же пользуется диагностика."""
        return await self._request(
            'GET', f'/api/bandwidth-stats/nodes/{node_uuid}/users',
            params={'start': start.strftime('%Y-%m-%d'),
                    'end': end.strftime('%Y-%m-%d'),
                    'topUsersLimit': top})

    async def connection_keys(self, uuid: str, user_id: int | None = None) -> list[str]:
        """Готовые ссылки подключения: vless://, ss:// и прочие.

        Ручка в разных версиях панели принимает то uuid, то числовой id,
        поэтому пробуем оба — второй только если первый ответил 404.
        """
        attempts = [value for value in (uuid, user_id) if value]
        last: Exception | None = None
        for value in attempts:
            try:
                data = await self._request(
                    'GET', f'/api/subscriptions/connection-keys/{value}')
            except VpnPanelError as exc:
                last = exc
                if 'HTTP 404' not in str(exc):
                    raise
                continue
            return _links(data)

        if last:
            raise last
        return []

    async def devices(self, ref: str | int) -> list[dict]:
        if not ref:
            return []
        data = await self._request('GET', f'/api/hwid/devices/{ref}')
        return (data or {}).get('devices') or []

    async def delete_subscription(self, ref: str | int) -> bool:
        """Удалить пользователя панели. Нужно только тестовым аккаунтам.

        Без этого удаление из базы бота бесполезно: shortUuid считается от
        user_id, и при повторной регистрации панель ответит «User short UUID
        already exists» — тот же отказ, что ловили при продлении.

        Нет такого пользователя — это успех, а не ошибка: цель достигнута.
        """
        if not ref:
            return False
        try:
            await self._request('DELETE', f'/api/users/{ref}')
            return True
        except VpnPanelError as exc:
            if '404' in str(exc):
                return True
            log.warning('подписка %s не удалена из панели: %s', ref, exc)
            return False

    async def delete_device(self, ref: str | int, hwid: str) -> bool:
        """Отвязать устройство. «Уже нет» считается успехом.

        Панель отвечает 404 A204 «HWID device not found», если устройство
        отвязали параллельно или список на экране устарел. Цель — чтобы
        устройства не было, и она достигнута: ошибкой это не является.
        """
        try:
            field = 'userId' if is_numeric_ref(ref) else 'userUuid'
            await self._request('POST', '/api/hwid/devices/delete',
                                json={field: ref, 'hwid': hwid})
            return True
        except VpnPanelError as exc:
            # A204 — код именно этого случая. По одному слову «not found»
            # судить нельзя: так же выглядит 404 на неверный путь, и его
            # молчаливое «успешно» скрыло бы поломку интеграции.
            if 'A204' in str(exc) or 'HWID device not found' in str(exc):
                log.debug('устройство %s у %s уже отвязано', hwid, ref)
                return True
            log.warning('не удалось отвязать устройство %s у %s: %s', hwid, ref, exc)
            return False

    # ── настройки с запасным значением ──────────────────────────────────────
    async def _setting_int(self, key: str, default: int) -> int:
        return await self._settings.int(key) if self._settings else default

    async def _setting_str(self, key: str, default: str = '') -> str:
        return str(await self._settings.get(key, default)) if self._settings else default
