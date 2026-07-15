"""Admin authentication: one or two static admins, JWT in an httpOnly cookie."""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status

from .config import Settings, get_settings

log = logging.getLogger("app.auth")

_ephemeral_secret: str | None = None


def jwt_secret() -> str:
    """Configured secret, or a per-process ephemeral one (dev convenience)."""
    global _ephemeral_secret
    settings = get_settings()
    if settings.jwt_secret:
        return settings.jwt_secret
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        log.warning("JWT_SECRET is not set — using an ephemeral secret; "
                    "sessions will not survive restarts")
    return _ephemeral_secret


def _check_password(plain: str, bcrypt_hash: str, fallback_plain: str) -> bool:
    if bcrypt_hash:
        try:
            return bcrypt.checkpw(plain.encode(), bcrypt_hash.encode())
        except ValueError:
            return False
    if fallback_plain:
        return hmac.compare_digest(plain, fallback_plain)
    return False


def verify_credentials(username: str, password: str,
                       settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if hmac.compare_digest(username, s.admin_username):
        return _check_password(password, s.admin_password_hash, s.admin_password)
    if s.admin2_username and hmac.compare_digest(username, s.admin2_username):
        return _check_password(password, s.admin2_password_hash, "")
    return False


def create_token(username: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now,
               "exp": now + timedelta(hours=s.jwt_ttl_hours)}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def get_current_admin(request: Request) -> str:
    token = request.cookies.get(get_settings().cookie_name)
    username = decode_token(token) if token else None
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return username


require_admin = Depends(get_current_admin)


class LoginThrottle:
    """Naive in-memory brute-force protection: 5 failures → 5 min lockout."""

    MAX_FAILS = 5
    WINDOW = 300.0

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def check(self, ip: str) -> None:
        now = time.monotonic()
        fails = [t for t in self._fails.get(ip, []) if now - t < self.WINDOW]
        self._fails[ip] = fails
        if len(fails) >= self.MAX_FAILS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Too many attempts, try again later")

    def record_failure(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


throttle = LoginThrottle()
