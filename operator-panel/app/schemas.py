"""Pydantic request/response models for all endpoints."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------- auth

class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    operator: "OperatorPublic"


# ---------------------------------------------------------------- operators (owner)

def _validate_permission_keys(v: dict[str, bool] | None) -> dict[str, bool] | None:
    if v is None:
        return v
    from .security import PERMISSIONS
    unknown = set(v) - set(PERMISSIONS)
    if unknown:
        raise ValueError(f"Неизвестные права: {', '.join(sorted(unknown))}")
    return {k: bool(val) for k, val in v.items()}


class OperatorCreate(BaseModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    role: Literal["operator", "owner"] = "operator"
    permissions: dict[str, bool] | None = Field(
        default=None, description="Права оператора; None = все разрешены")

    _perm_keys = field_validator("permissions")(_validate_permission_keys)


class OperatorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["operator", "owner"] | None = None
    active: bool | None = None
    permissions: dict[str, bool] | None = None

    _perm_keys = field_validator("permissions")(_validate_permission_keys)


class OperatorPublic(BaseModel):
    id: str
    login: str
    name: str
    role: str
    active: bool
    permissions: dict[str, bool]  # effective (resolved) permission map
    created_at: str | None = None
    last_login_at: str | None = None


# ---------------------------------------------------------------- user actions
# Every mutating action requires a human-entered reason — it goes to the audit log.

class ReasonMixin(BaseModel):
    reason: str = Field(min_length=3, max_length=500, description="Причина действия (обязательно)")

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Укажите содержательную причину (минимум 3 символа)")
        return v


class BalanceChangeRequest(ReasonMixin):
    """amount > 0 — начислить, amount < 0 — списать.
    Баланс никогда не может уйти в минус — списание больше остатка отклоняется."""
    amount: float

    @field_validator("amount")
    @classmethod
    def amount_not_zero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("Сумма не может быть нулевой")
        if abs(v) > 100_000_000:
            raise ValueError("Сумма выглядит неправдоподобно большой")
        return v


class SubscriptionExpireRequest(ReasonMixin):
    """Либо days (сдвиг на ±N дней), либо expire_at (точная дата ISO)."""
    days: int | None = Field(default=None, ge=-3650, le=3650)
    expire_at: str | None = None  # ISO 8601

    @field_validator("expire_at")
    @classmethod
    def validate_iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from .utils import parse_any_ts
        if parse_any_ts(v) is None:
            raise ValueError("Неверный формат даты, ожидается ISO 8601")
        return v


class DeviceLimitRequest(ReasonMixin):
    limit: int = Field(ge=0, le=1000)


class BypassUpdateRequest(ReasonMixin):
    """Обновление лимита трафика ByPass. Срок ByPass всегда равен сроку подписки
    и отдельно не меняется."""
    traffic_limit_gb: float | None = Field(default=None, ge=0, le=1_000_000)
    add_traffic_gb: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)


class DeviceResetRequest(ReasonMixin):
    hwid: str | None = Field(
        default=None, max_length=256,
        description="Конкретное устройство; пусто = отвязать все",
    )


class GiftRequest(ReasonMixin):
    days: int | None = Field(default=None, ge=1, le=3650)
    bypass_gb: float | None = Field(default=None, gt=0, le=1_000_000)


class ForceLocalMixin(BaseModel):
    """Если Remnawave недоступна, оператор может явно применить изменение только в MongoDB."""
    force_local: bool = False


class SubscriptionExpireRequestFull(SubscriptionExpireRequest, ForceLocalMixin):
    pass


class DeviceLimitRequestFull(DeviceLimitRequest, ForceLocalMixin):
    pass


class BypassUpdateRequestFull(BypassUpdateRequest, ForceLocalMixin):
    pass


class GiftRequestFull(GiftRequest, ForceLocalMixin):
    pass
