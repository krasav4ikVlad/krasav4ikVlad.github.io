"""POST /api/auth/login, GET /api/auth/me"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..audit import ACTION_LOGIN, ACTION_PASSWORD_CHANGE, write_audit
from ..config import get_settings
from ..database import get_db
from ..schemas import ChangePasswordRequest, LoginRequest, OperatorPublic, TokenResponse
from ..security import (
    CurrentOperatorAnyState,
    check_login_allowed,
    client_ip,
    create_access_token,
    hash_password,
    register_failed_login,
    invalidate_operator_cache,
    reset_login_attempts,
    resolved_permissions,
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
        must_change_password=bool(op.get("must_change_password")),
        permissions=resolved_permissions(op),
        created_at=to_iso_z(op["created_at"]) if op.get("created_at") else None,
        last_login_at=to_iso_z(op["last_login_at"]) if op.get("last_login_at") else None,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    ip = client_ip(request)
    check_login_allowed(body.login, ip)

    settings = get_settings()
    operators = get_db()[settings.operators_collection]
    operator = await operators.find_one({"login": body.login.strip().lower()})

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
async def me(operator: CurrentOperatorAnyState):
    return operator_public(operator)


@router.post("/change-password", response_model=OperatorPublic)
async def change_password(body: ChangePasswordRequest, request: Request,
                          operator: CurrentOperatorAnyState):
    """Смена собственного пароля. Обязательна после входа с временным паролем."""
    if not verify_password(body.current_password, operator.get("password_hash", "")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Текущий пароль неверен")
    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Новый пароль совпадает с текущим")

    settings = get_settings()
    await get_db()[settings.operators_collection].update_one(
        {"_id": operator["_id"]},
        {"$set": {"password_hash": hash_password(body.new_password),
                  "must_change_password": False}},
    )
    invalidate_operator_cache(str(operator["_id"]))
    await write_audit(operator=operator, action=ACTION_PASSWORD_CHANGE,
                      target_user_id=None, ip=client_ip(request))
    updated = {**operator, "must_change_password": False}
    return operator_public(updated)
