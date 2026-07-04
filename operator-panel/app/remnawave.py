"""Async client for the Remnawave panel REST API.

Endpoints used (Remnawave v1.x):
    GET    /api/users/{uuid}                  — fetch user
    PATCH  /api/users                         — update user (body includes uuid)
    GET    /api/hwid/devices/{userUuid}       — list bound HWID devices
    POST   /api/hwid/devices/delete           — unbind one device {userUuid, hwid}

If your Remnawave version uses different paths, adjust them here — everything else
in the app talks only to this module.

Field mapping (MongoDB -> Remnawave user):
    vpn.uuid                     -> uuid
    vpn.expireAt                 -> expireAt
    vpn.hwidDeviceLimit          -> hwidDeviceLimit
    vpn.bypass_trafficLimitBytes -> trafficLimitBytes
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


class RemnawaveError(Exception):
    """Any failure while talking to Remnawave. .message is safe to show operators."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RemnawaveClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.remnawave_base_url.rstrip("/")
        self.token = settings.remnawave_token
        self.timeout = settings.remnawave_timeout_sec

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, json: Any = None) -> dict:
        if not self.configured:
            raise RemnawaveError("Remnawave API не настроена (REMNAWAVE_BASE_URL / REMNAWAVE_TOKEN)")
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=self._headers(), json=json)
        except httpx.TimeoutException:
            raise RemnawaveError("Remnawave не ответила вовремя (timeout)")
        except httpx.HTTPError as e:
            raise RemnawaveError(f"Ошибка соединения с Remnawave: {e.__class__.__name__}")

        if resp.status_code == 401:
            raise RemnawaveError("Remnawave: неверный API-токен", 401)
        if resp.status_code == 404:
            raise RemnawaveError("Remnawave: пользователь не найден в панели", 404)
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("message") or str(body.get("errors", ""))[:300]
            except Exception:
                detail = resp.text[:300]
            raise RemnawaveError(f"Remnawave HTTP {resp.status_code}: {detail}", resp.status_code)
        try:
            data = resp.json()
        except Exception:
            raise RemnawaveError("Remnawave вернула невалидный JSON")
        return data.get("response", data)

    # ------------------------------------------------------------- users

    async def get_user(self, uuid: str) -> dict:
        return await self._request("GET", f"/api/users/{uuid}")

    async def update_user(
        self,
        uuid: str,
        *,
        expire_at: str | None = None,
        hwid_device_limit: int | None = None,
        traffic_limit_bytes: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"uuid": uuid}
        if expire_at is not None:
            body["expireAt"] = expire_at
        if hwid_device_limit is not None:
            body["hwidDeviceLimit"] = hwid_device_limit
        if traffic_limit_bytes is not None:
            body["trafficLimitBytes"] = traffic_limit_bytes
        if len(body) == 1:
            return {}
        return await self._request("PATCH", "/api/users", json=body)

    # ------------------------------------------------------------- HWID devices

    async def get_devices(self, user_uuid: str) -> list[dict]:
        data = await self._request("GET", f"/api/hwid/devices/{user_uuid}")
        if isinstance(data, list):
            return data
        return data.get("devices", []) if isinstance(data, dict) else []

    async def delete_device(self, user_uuid: str, hwid: str) -> None:
        await self._request("POST", "/api/hwid/devices/delete", json={"userUuid": user_uuid, "hwid": hwid})

    async def delete_all_devices(self, user_uuid: str) -> int:
        """Unbind every device; returns how many were removed."""
        devices = await self.get_devices(user_uuid)
        removed = 0
        errors: list[str] = []
        for device in devices:
            hwid = device.get("hwid")
            if not hwid:
                continue
            try:
                await self.delete_device(user_uuid, hwid)
                removed += 1
            except RemnawaveError as e:
                errors.append(f"{hwid}: {e.message}")
        if errors:
            raise RemnawaveError(
                f"Отвязано {removed} из {len(devices)}; ошибки: {'; '.join(errors[:3])}"
            )
        return removed


def get_remnawave() -> RemnawaveClient:
    return RemnawaveClient()
