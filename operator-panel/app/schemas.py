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
    """Пароль не задаётся вручную: сервер генерирует временный,
    оператор обязан сменить его при первом входе."""
    login: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=128)
    role: Literal["operator", "owner"] = "operator"
    permissions: dict[str, bool] | None = Field(
        default=None, description="Права оператора; None = все разрешены")

    _perm_keys = field_validator("permissions")(_validate_permission_keys)


class OperatorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    role: Literal["operator", "owner"] | None = None
    active: bool | None = None
    permissions: dict[str, bool] | None = None
    # для расчёта зарплаты по активности (страница «Активность»)
    salary_base: int | None = Field(default=None, ge=0, le=10_000_000)   # оклад, ₽/мес
    hours_per_week: float | None = Field(default=None, ge=0, le=84)      # график, ч/нед (потолок 84: завышенные часы ломают норму активности)
    # недельный график: {"mon": "09:00-18:00", "tue": "", ...}; пусто = выходной,
    # конец меньше начала = смена через полночь; часы/нед считаются автоматически
    schedule: dict[str, str] | None = None
    # username в Telegram (без @) — чтобы ответы из TG-треда засчитывались
    # этому же оператору в «Активности», а не отдельной строкой
    tg_username: str | None = Field(default=None, max_length=64)

    _perm_keys = field_validator("permissions")(_validate_permission_keys)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class OperatorPublic(BaseModel):
    id: str
    login: str
    name: str
    role: str
    active: bool
    must_change_password: bool = False
    permissions: dict[str, bool]  # effective (resolved) permission map
    created_at: str | None = None
    last_login_at: str | None = None
    salary_base: int | None = None
    hours_per_week: float | None = None
    schedule: dict[str, str] | None = None
    tg_username: str | None = None


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


class ForceLocalMixin(BaseModel):
    """Если Remnawave недоступна, оператор может явно применить изменение только в MongoDB."""
    force_local: bool = False


class SubscriptionExpireRequestFull(SubscriptionExpireRequest, ForceLocalMixin):
    pass


class DeviceLimitRequestFull(DeviceLimitRequest, ForceLocalMixin):
    pass


class BypassUpdateRequestFull(BypassUpdateRequest, ForceLocalMixin):
    pass

