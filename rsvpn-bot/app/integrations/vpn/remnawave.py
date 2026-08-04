"""Единственное место, которое ходит в панель Remnawave по HTTP.

Сейчас вызовы разбросаны по utils.py (create_vpn_subscription,
create_bypass_vpn_subscription, update_user_subscription, renew_vpn_config,
fetch_hwid_devices, remna_delete_hwid) и в каждом свой формат ошибок.
Здесь один клиент: общий httpx-клиент, общий разбор ответа, свои исключения.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.errors import VpnPanelError

log = logging.getLogger(__name__)


class RemnawaveClient:
    def __init__(self, base_url: str, token: str, http, base_squad_id: str = ''):
        self._base = base_url.rstrip('/')
        self._token = token
        self._http = http           # httpx.AsyncClient, создаётся в контейнере
        self._base_squad = base_squad_id

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, f'{self._base}{path}',
                headers={'Authorization': f'Bearer {self._token}'},
                **kwargs,
            )
        except Exception as exc:  # сетевые ошибки
            raise VpnPanelError(f'{method} {path}: {exc}') from exc

        if response.status_code >= 400:
            raise VpnPanelError(f'{method} {path}: HTTP {response.status_code} {response.text[:200]}')

        payload = response.json()
        return payload.get('response', payload)

    # ── подписки ────────────────────────────────────────────────────────────
    async def create_subscription(self, user_id: int, days: int) -> dict:
        raise NotImplementedError('перенести тело из utils.create_vpn_subscription')

    async def renew_subscription(self, uuid: str, days: int) -> dict:
        raise NotImplementedError('перенести тело из utils.renew_vpn_config')

    async def update_subscription(self, uuid: str, **fields) -> dict:
        raise NotImplementedError('перенести тело из utils.update_user_subscription')

    # ── устройства ──────────────────────────────────────────────────────────
    async def devices(self, uuid: str) -> list[dict]:
        raise NotImplementedError('перенести тело из utils.fetch_hwid_devices')

    async def delete_device(self, uuid: str, hwid: str) -> bool:
        raise NotImplementedError('перенести тело из utils.remna_delete_hwid')
