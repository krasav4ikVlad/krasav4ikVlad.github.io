"""POST /api/auth/login, GET /api/auth/me"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..audit import ACTION_LOGIN, write_audit
from ..config import get_settings
from ..database import get_db
from ..schemas import LoginRequest, OperatorPublic, TokenResponse
from ..security import (
    CurrentOperator,
    check_login_allowed,
    client_ip,
    create_access_token,
    register_failed_login,
    reset_login_attempts,
    verify_password,
)
from ..utils import to_iso_z, utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])


def operator_public(op: dict) -> OperatorPublic:
    return OperatorPublic(
        id=str(op["_id"]),
        login=op["login"],
        name=op.get("name", ""),
        role=op.get("role", "operator"),
        active=op.get("active", False),
        created_at=to_iso_z(op["created_at"]) if op.get("created_at") else None,
        last_login_at=to_iso_z(op["last_login_at"]) if op.get("last_login_at") else None,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    ip = client_ip(request)
    check_login_allowed(body.login, ip)

    settings = get_settings()
    operators = get_db()[settings.operators_collection]
    operator = await operators.find_one({"login": body.login})

    if operator is None or not verify_password(body.password, operator.get("password_hash", "")):
        register_failed_login(body.login, ip)
        # Same message for both cases — don't leak which logins exist
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    if not operator.get("active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись отключена")

    reset_login_attempts(body.login, ip)
    await operators.update_one({"_id": operator["_id"]}, {"$set": {"last_login_at": utcnow()}})
    await write_audit(operator=operator, action=ACTION_LOGIN, target_user_id=None, ip=ip)

    return TokenResponse(access_token=create_access_token(operator), operator=operator_public(operator))


@router.get("/me", response_model=OperatorPublic)
async def me(operator: CurrentOperator):
    return operator_public(operator)
