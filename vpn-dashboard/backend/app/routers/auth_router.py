"""Login / logout / whoami."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth import (create_token, get_current_admin, throttle,
                    verify_credentials)
from ..config import get_settings

log = logging.getLogger("app.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    throttle.check(ip)
    if not verify_credentials(body.username, body.password):
        throttle.record_failure(ip)
        log.warning("failed login attempt", extra={"ip": ip,
                                                   "username": body.username})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    throttle.reset(ip)
    s = get_settings()
    response.set_cookie(
        key=s.cookie_name,
        value=create_token(body.username),
        max_age=s.jwt_ttl_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    log.info("admin logged in", extra={"username": body.username, "ip": ip})
    return {"ok": True, "username": body.username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(get_settings().cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    return {"username": get_current_admin(request)}
