"""Authentication: bcrypt password hashing, JWT issuing/verification, role guards,
and a small in-memory brute-force limiter for the login endpoint."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Annotated

import bcrypt
import jwt
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .database import get_db
from .utils import utcnow

ROLE_OPERATOR = "operator"
ROLE_OWNER = "owner"

# Granular operator permissions. Keys match audit action types.
# View access (search / card / histories) is always allowed for any active account.
PERMISSIONS: dict[str, str] = {
    "balance_change": "Изменение баланса",
    "subscription_expire_change": "Срок подписки",
    "device_limit_change": "Лимит устройств",
    "bypass_update": "ByPass (срок и трафик)",
    "device_reset": "Отвязка устройств",
    "email_change": "Изменение email",
    "gift": "Подарки (дни / ГБ)",
}


def resolved_permissions(operator: dict) -> dict[str, bool]:
    """Effective permission map. Owner → everything; operator without an explicit
    `permissions` field (legacy account) → everything; otherwise per-key flags."""
    if operator.get("role") == ROLE_OWNER:
        return {k: True for k in PERMISSIONS}
    stored = operator.get("permissions")
    if stored is None:
        return {k: True for k in PERMISSIONS}
    return {k: bool(stored.get(k)) for k in PERMISSIONS}


def ensure_permission(operator: dict, key: str) -> None:
    if not resolved_permissions(operator).get(key, False):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"У вашей учётной записи нет права «{PERMISSIONS.get(key, key)}». "
            f"Обратитесь к владельцу.",
        )

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


# ---------------------------------------------------------------- JWT

def create_access_token(operator: dict) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(operator["_id"]),
        "login": operator["login"],
        "role": operator["role"],
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + settings.access_token_ttl_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, войдите заново")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")


# ---------------------------------------------------------------- request context

def client_ip(request: Request) -> str:
    """Real client IP behind a reverse proxy (nginx must set X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_current_operator(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """Resolve the JWT to a live operator document (so deactivation applies immediately)."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация")
    payload = decode_token(credentials.credentials)
    settings = get_settings()
    try:
        oid = ObjectId(payload["sub"])
    except (InvalidId, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")
    operator = await get_db()[settings.operators_collection].find_one({"_id": oid})
    if operator is None or not operator.get("active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись отключена")
    return operator


async def require_owner(operator: Annotated[dict, Depends(get_current_operator)]) -> dict:
    if operator.get("role") != ROLE_OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только владельцу")
    return operator


CurrentOperator = Annotated[dict, Depends(get_current_operator)]
OwnerOperator = Annotated[dict, Depends(require_owner)]


# ---------------------------------------------------------------- login rate limiting

_attempts: dict[str, list[float]] = defaultdict(list)


def check_login_allowed(login: str, ip: str) -> None:
    settings = get_settings()
    key = f"{login.lower()}|{ip}"
    now = time.monotonic()
    window = settings.login_attempt_window_sec
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= settings.login_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток входа. Попробуйте позже.",
        )


def register_failed_login(login: str, ip: str) -> None:
    _attempts[f"{login.lower()}|{ip}"].append(time.monotonic())


def reset_login_attempts(login: str, ip: str) -> None:
    _attempts.pop(f"{login.lower()}|{ip}", None)
