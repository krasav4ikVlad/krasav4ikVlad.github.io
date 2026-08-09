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
from app.core.time import now

log = logging.getLogger(__name__)


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

        if response.status_code not in (200, 201):
            text = getattr(response, 'text', '')
            raise VpnPanelError(f'{method} {path}: HTTP {response.status_code} {text[:300]}')

        try:
            payload = response.json()
        except Exception as exc:
            raise VpnPanelError(f'{method} {path}: ответ не JSON') from exc

        return payload.get('response', payload) if isinstance(payload, dict) else payload

    # ── подписки ────────────────────────────────────────────────────────────
    async def create_subscription(self, user_id: int, days: int) -> dict:
        started = now()
        device_limit = await self._setting_int('price.default_device_limit', 2)
        squads = await self._squads.for_new_user() if self._squads else []

        return await self._request('POST', '/api/users', json={
            'username': str(user_id),
            'status': 'ACTIVE',
            'shortUuid': subscription_token(user_id),
            'expireAt': (started + timedelta(days=days)).isoformat(),
            'createdAt': started.isoformat(),
            'telegramId': user_id,
            'hwidDeviceLimit': device_limit,
            'activeInternalSquads': squads,
        })

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

        return await self._request('POST', '/api/users', json=payload)

    async def update_subscription(self, uuid: str, *, expire_at: datetime | str | None = None,
                                  traffic_bytes: int | None = None,
                                  device_limit: int | None = None,
                                  squads: list[str] | None = None,
                                  status: str | None = None) -> dict:
        """Частичный PATCH: передаются только заданные поля."""
        payload: dict = {'uuid': uuid}

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

        return await self._request('PATCH', '/api/users', json=payload)

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
    async def set_status(self, uuid: str, status: str) -> dict:
        """ACTIVE / DISABLED — включить или отключить подписку в панели.

        Нужно жёсткой блокировке: обычный бан только закрывает бота, а
        конфиг у человека продолжает работать, пока не истечёт срок.
        """
        return await self.update_subscription(uuid, status=status)

    async def get_subscription(self, uuid: str) -> dict:
        """Пользователь панели целиком: трафик, статус, когда был онлайн.

        Нужен владельцу личного сервера: без этого «статистика сервера» — это
        список имён без единой цифры.
        """
        if not uuid:
            return {}
        return await self._request('GET', f'/api/users/{uuid}') or {}

    async def devices(self, uuid: str) -> list[dict]:
        if not uuid:
            return []
        data = await self._request('GET', f'/api/hwid/devices/{uuid}')
        return (data or {}).get('devices') or []

    async def delete_subscription(self, uuid: str) -> bool:
        """Удалить пользователя панели. Нужно только тестовым аккаунтам.

        Без этого удаление из базы бота бесполезно: shortUuid считается от
        user_id, и при повторной регистрации панель ответит «User short UUID
        already exists» — тот же отказ, что ловили при продлении.

        Нет такого пользователя — это успех, а не ошибка: цель достигнута.
        """
        if not uuid:
            return False
        try:
            await self._request('DELETE', f'/api/users/{uuid}')
            return True
        except VpnPanelError as exc:
            if '404' in str(exc):
                return True
            log.warning('подписка %s не удалена из панели: %s', uuid, exc)
            return False

    async def delete_device(self, uuid: str, hwid: str) -> bool:
        """Отвязать устройство. «Уже нет» считается успехом.

        Панель отвечает 404 A204 «HWID device not found», если устройство
        отвязали параллельно или список на экране устарел. Цель — чтобы
        устройства не было, и она достигнута: ошибкой это не является.
        """
        try:
            await self._request('POST', '/api/hwid/devices/delete',
                                json={'userUuid': uuid, 'hwid': hwid})
            return True
        except VpnPanelError as exc:
            # A204 — код именно этого случая. По одному слову «not found»
            # судить нельзя: так же выглядит 404 на неверный путь, и его
            # молчаливое «успешно» скрыло бы поломку интеграции.
            if 'A204' in str(exc) or 'HWID device not found' in str(exc):
                log.debug('устройство %s у %s уже отвязано', hwid, uuid)
                return True
            log.warning('не удалось отвязать устройство %s у %s: %s', hwid, uuid, exc)
            return False

    # ── настройки с запасным значением ──────────────────────────────────────
    async def _setting_int(self, key: str, default: int) -> int:
        return await self._settings.int(key) if self._settings else default

    async def _setting_str(self, key: str, default: str = '') -> str:
        return str(await self._settings.get(key, default)) if self._settings else default
