"""Mutating operator actions.

Invariants:
  * Every action requires a `reason` and writes an audit record (old -> new).
  * Balance changes are a single atomic findOneAndUpdate ($inc) — no read-modify-write.
  * Subscription / devices / traffic: Remnawave is updated FIRST, MongoDB second.
    If Remnawave fails the operation aborts (HTTP 502) and Mongo is untouched — no drift.
    The operator may retry with force_local=true to apply Mongo-only (audited as unsynced).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pymongo import ReturnDocument

from .. import audit
from ..audit import write_audit
from ..remnawave import RemnawaveError, get_remnawave
from ..schemas import (
    BalanceChangeRequest,
    BypassUpdateRequestFull,
    DeviceLimitRequestFull,
    DeviceResetRequest,
    EmailChangeRequest,
    GiftRequestFull,
    SubscriptionExpireRequestFull,
)
from ..security import CurrentOperator, client_ip
from ..user_service import find_user_or_404, users_col
from ..utils import GB, bot_ts_now, jsonable, parse_any_ts, shift_expire, to_iso_z, utcnow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["actions"])


# =============================================================== balance

@router.post("/{user_id}/balance")
async def change_balance(user_id: int, body: BalanceChangeRequest, request: Request,
                         operator: CurrentOperator):
    """Начислить (amount > 0) или списать (amount < 0) любую сумму. Атомарно."""
    amount = round(float(body.amount), 2)
    if amount == int(amount):
        amount = int(amount)

    history_entry = {
        "amount": amount,
        "details": f"Оператор {operator['login']}: {body.reason}",
        "timestamp": bot_ts_now(),
    }
    query: dict = {"user_data.user_id": user_id}
    if amount < 0 and not body.allow_negative:
        query["info.balance"] = {"$gte": -amount}

    before = await users_col().find_one_and_update(
        query,
        {"$inc": {"info.balance": amount}, "$push": {"info.logs_balance": history_entry}},
        projection={"info.balance": 1},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        # Either the user doesn't exist or the guarded deduction would go negative
        doc = await find_user_or_404(user_id, projection={"info.balance": 1})
        current = (doc.get("info") or {}).get("balance", 0)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Недостаточно средств: баланс {current}, списание {-amount}. "
            f"Включите «разрешить минус», если списание нужно провести всё равно.",
        )

    old_balance = (before.get("info") or {}).get("balance", 0)
    new_balance = round(old_balance + amount, 2)
    await write_audit(
        operator=operator, action=audit.ACTION_BALANCE_CHANGE, target_user_id=user_id,
        old_value={"balance": old_balance}, new_value={"balance": new_balance},
        reason=body.reason, ip=client_ip(request),
        extra={"amount": amount, "allow_negative": body.allow_negative},
    )
    return {"ok": True, "old_balance": old_balance, "new_balance": new_balance}


# =============================================================== remnawave helper

async def _sync_remnawave(
    *, vpn: dict, force_local: bool,
    expire_at: str | None = None,
    hwid_device_limit: int | None = None,
    traffic_limit_bytes: int | None = None,
) -> tuple[bool | None, str | None]:
    """Push changes to Remnawave BEFORE Mongo. Returns (synced, error_text).

    Raises 502 when sync fails and force_local is not set — Mongo stays untouched.
    """
    uuid = vpn.get("uuid")
    if not uuid:
        if force_local:
            return None, "У пользователя нет vpn.uuid — изменение применено только в базе"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "У пользователя нет VPN-подписки (vpn.uuid отсутствует). "
            "Используйте force_local, чтобы изменить только запись в базе.",
        )
    try:
        await get_remnawave().update_user(
            uuid,
            expire_at=expire_at,
            hwid_device_limit=hwid_device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
        )
        return True, None
    except RemnawaveError as e:
        if force_local:
            log.warning("Remnawave sync skipped (force_local): %s", e.message)
            return False, e.message
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Изменение НЕ применено: {e.message}. "
            f"Повторите позже или используйте force_local (только база, без нод).",
        )


# =============================================================== subscription expiry

@router.post("/{user_id}/subscription/expire")
async def change_subscription_expire(user_id: int, body: SubscriptionExpireRequestFull,
                                     request: Request, operator: CurrentOperator):
    """Продлить/сократить подписку на N дней или задать точную дату."""
    if (body.days is None) == (body.expire_at is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Укажите ровно одно: days или expire_at")

    doc = await find_user_or_404(user_id, projection={"vpn": 1})
    vpn = doc.get("vpn") or {}
    old_expire = vpn.get("expireAt")

    if body.days is not None:
        new_dt = shift_expire(old_expire, body.days)
    else:
        new_dt = parse_any_ts(body.expire_at)  # validated in schema
    new_iso = to_iso_z(new_dt)

    synced, sync_err = await _sync_remnawave(vpn=vpn, force_local=body.force_local,
                                             expire_at=new_iso)
    # The bot stores expireAt as a native BSON Date (it calls .strftime on it) —
    # write a datetime, never a string, or the bot crashes rendering the date.
    await users_col().update_one({"user_data.user_id": user_id},
                                 {"$set": {"vpn.expireAt": new_dt}})
    await write_audit(
        operator=operator, action=audit.ACTION_SUB_EXPIRE, target_user_id=user_id,
        old_value={"expireAt": jsonable(old_expire)}, new_value={"expireAt": new_iso},
        reason=body.reason, ip=client_ip(request),
        remnawave_synced=synced, remnawave_error=sync_err,
        extra={"days": body.days} if body.days is not None else None,
    )
    return {"ok": True, "old_expire_at": jsonable(old_expire), "new_expire_at": new_iso,
            "remnawave_synced": synced, "remnawave_error": sync_err}


# =============================================================== device limit

@router.post("/{user_id}/subscription/device-limit")
async def change_device_limit(user_id: int, body: DeviceLimitRequestFull,
                              request: Request, operator: CurrentOperator):
    doc = await find_user_or_404(user_id, projection={"vpn": 1})
    vpn = doc.get("vpn") or {}
    old_limit = vpn.get("hwidDeviceLimit")
    if old_limit == body.limit:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Лимит уже равен {body.limit}")

    synced, sync_err = await _sync_remnawave(vpn=vpn, force_local=body.force_local,
                                             hwid_device_limit=body.limit)
    await users_col().update_one({"user_data.user_id": user_id},
                                 {"$set": {"vpn.hwidDeviceLimit": body.limit}})
    await write_audit(
        operator=operator, action=audit.ACTION_DEVICE_LIMIT, target_user_id=user_id,
        old_value={"hwidDeviceLimit": old_limit}, new_value={"hwidDeviceLimit": body.limit},
        reason=body.reason, ip=client_ip(request),
        remnawave_synced=synced, remnawave_error=sync_err,
    )
    return {"ok": True, "old_limit": old_limit, "new_limit": body.limit,
            "remnawave_synced": synced, "remnawave_error": sync_err}


# =============================================================== bypass

@router.post("/{user_id}/bypass")
async def update_bypass(user_id: int, body: BypassUpdateRequestFull,
                        request: Request, operator: CurrentOperator):
    """Срок ByPass и/или лимит трафика. traffic_limit_gb — абсолютное значение,
    add_traffic_gb — прибавка (может быть отрицательной)."""
    if body.traffic_limit_gb is not None and body.add_traffic_gb is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Укажите либо traffic_limit_gb, либо add_traffic_gb")
    if body.days is not None and body.expire_at is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Укажите либо days, либо expire_at")
    if all(v is None for v in (body.days, body.expire_at, body.traffic_limit_gb, body.add_traffic_gb)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет изменений")

    doc = await find_user_or_404(user_id, projection={"vpn": 1})
    vpn = doc.get("vpn") or {}
    old_expire = vpn.get("bypass_expireAt")
    old_bytes = vpn.get("bypass_trafficLimitBytes") or 0

    updates: dict = {}
    new_expire_dt = None
    if body.days is not None:
        new_expire_dt = shift_expire(old_expire, body.days)
    elif body.expire_at is not None:
        new_expire_dt = parse_any_ts(body.expire_at)
    if new_expire_dt:
        # native datetime — same reason as vpn.expireAt: the bot strftime's it
        updates["vpn.bypass_expireAt"] = new_expire_dt

    new_bytes = None
    if body.traffic_limit_gb is not None:
        new_bytes = int(body.traffic_limit_gb * GB)
    elif body.add_traffic_gb is not None:
        new_bytes = max(0, int(old_bytes + body.add_traffic_gb * GB))
    if new_bytes is not None:
        updates["vpn.bypass_trafficLimitBytes"] = new_bytes

    # Remnawave user's trafficLimitBytes mirrors the bypass traffic limit;
    # bypass expiry is bot-side logic and lives only in MongoDB.
    synced, sync_err = None, None
    if new_bytes is not None:
        synced, sync_err = await _sync_remnawave(vpn=vpn, force_local=body.force_local,
                                                 traffic_limit_bytes=new_bytes)

    await users_col().update_one({"user_data.user_id": user_id}, {"$set": updates})
    await write_audit(
        operator=operator, action=audit.ACTION_BYPASS_UPDATE, target_user_id=user_id,
        old_value={"bypass_expireAt": jsonable(old_expire), "bypass_trafficLimitBytes": old_bytes},
        new_value={"bypass_expireAt": jsonable(new_expire_dt or old_expire),
                   "bypass_trafficLimitBytes": new_bytes if new_bytes is not None else old_bytes},
        reason=body.reason, ip=client_ip(request),
        remnawave_synced=synced, remnawave_error=sync_err,
    )
    return {"ok": True, "updates": jsonable(updates),
            "remnawave_synced": synced, "remnawave_error": sync_err}


# =============================================================== devices

@router.get("/{user_id}/devices")
async def list_devices(user_id: int, _op: CurrentOperator = None):
    """Актуальный список привязанных устройств — читаем из Remnawave (source of truth)."""
    doc = await find_user_or_404(user_id, projection={"vpn.uuid": 1, "vpn.hwidDeviceLimit": 1,
                                                      "vpn.extraDevices": 1})
    vpn = doc.get("vpn") or {}
    uuid = vpn.get("uuid")
    if not uuid:
        return {"devices": [], "limit": vpn.get("hwidDeviceLimit"),
                "extra_devices": vpn.get("extraDevices", []),
                "warning": "У пользователя нет vpn.uuid"}
    try:
        devices = await get_remnawave().get_devices(uuid)
    except RemnawaveError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Не удалось получить устройства: {e.message}")
    return jsonable({"devices": devices, "limit": vpn.get("hwidDeviceLimit"),
                     "extra_devices": vpn.get("extraDevices", [])})


@router.post("/{user_id}/devices/reset")
async def reset_devices(user_id: int, body: DeviceResetRequest,
                        request: Request, operator: CurrentOperator):
    """Отвязать одно устройство (hwid) или все. Привязки живут в Remnawave —
    force_local здесь не имеет смысла, при ошибке операция просто не выполняется."""
    doc = await find_user_or_404(user_id, projection={"vpn.uuid": 1})
    uuid = (doc.get("vpn") or {}).get("uuid")
    if not uuid:
        raise HTTPException(status.HTTP_409_CONFLICT, "У пользователя нет VPN-подписки (vpn.uuid)")

    rw = get_remnawave()
    try:
        if body.hwid:
            await rw.delete_device(uuid, body.hwid)
            removed, target = 1, body.hwid
        else:
            removed = await rw.delete_all_devices(uuid)
            target = "all"
    except RemnawaveError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Отвязка не выполнена: {e.message}")

    await write_audit(
        operator=operator, action=audit.ACTION_DEVICE_RESET, target_user_id=user_id,
        old_value={"target": target}, new_value={"removed": removed},
        reason=body.reason, ip=client_ip(request), remnawave_synced=True,
    )
    return {"ok": True, "removed": removed}


# =============================================================== email

@router.post("/{user_id}/email")
async def change_email(user_id: int, body: EmailChangeRequest,
                       request: Request, operator: CurrentOperator):
    doc = await find_user_or_404(user_id, projection={"info.email": 1})
    old_email = (doc.get("info") or {}).get("email")
    new_email = str(body.email).lower()
    if old_email == new_email:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже установлен в это значение")

    await users_col().update_one({"user_data.user_id": user_id},
                                 {"$set": {"info.email": new_email}})
    await write_audit(
        operator=operator, action=audit.ACTION_EMAIL_CHANGE, target_user_id=user_id,
        old_value={"email": old_email}, new_value={"email": new_email},
        reason=body.reason, ip=client_ip(request),
    )
    return {"ok": True, "old_email": old_email, "new_email": new_email}


# =============================================================== gift

@router.post("/{user_id}/gift")
async def gift(user_id: int, body: GiftRequestFull, request: Request, operator: CurrentOperator):
    """Подарить дни подписки и/или гигабайты ByPass одним действием."""
    if body.days is None and body.bypass_gb is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Укажите days и/или bypass_gb")

    doc = await find_user_or_404(user_id, projection={"vpn": 1})
    vpn = doc.get("vpn") or {}
    old_expire = vpn.get("expireAt")
    old_bytes = vpn.get("bypass_trafficLimitBytes") or 0

    updates: dict = {}
    new_expire_dt = None
    new_expire_iso = None
    new_bytes = None
    if body.days is not None:
        new_expire_dt = shift_expire(old_expire, body.days)
        new_expire_iso = to_iso_z(new_expire_dt)
        updates["vpn.expireAt"] = new_expire_dt  # native datetime, the bot strftime's it
    if body.bypass_gb is not None:
        new_bytes = int(old_bytes + body.bypass_gb * GB)
        updates["vpn.bypass_trafficLimitBytes"] = new_bytes

    synced, sync_err = await _sync_remnawave(
        vpn=vpn, force_local=body.force_local,
        expire_at=new_expire_iso, traffic_limit_bytes=new_bytes,
    )
    await users_col().update_one({"user_data.user_id": user_id}, {"$set": updates})
    await write_audit(
        operator=operator, action=audit.ACTION_GIFT, target_user_id=user_id,
        old_value={"expireAt": jsonable(old_expire), "bypass_trafficLimitBytes": old_bytes},
        new_value={"expireAt": new_expire_iso or jsonable(old_expire),
                   "bypass_trafficLimitBytes": new_bytes if new_bytes is not None else old_bytes},
        reason=body.reason, ip=client_ip(request),
        remnawave_synced=synced, remnawave_error=sync_err,
        extra={"gift_days": body.days, "gift_bypass_gb": body.bypass_gb},
    )
    return {"ok": True, "updates": jsonable(updates),
            "remnawave_synced": synced, "remnawave_error": sync_err}
