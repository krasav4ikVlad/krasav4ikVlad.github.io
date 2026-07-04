"""Owner-only: manage operator accounts (create, edit, deactivate — never hard-delete,
history in the audit log must stay attributable)."""
from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError

from .. import audit
from ..audit import write_audit
from ..config import get_settings
from ..database import get_db
from ..schemas import OperatorCreate, OperatorPublic, OperatorUpdate
from ..security import OwnerOperator, client_ip, hash_password
from ..utils import utcnow
from .auth import operator_public

router = APIRouter(prefix="/api/operators", tags=["operators"])


def _col():
    return get_db()[get_settings().operators_collection]


def _oid(operator_id: str) -> ObjectId:
    try:
        return ObjectId(operator_id)
    except InvalidId:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")


@router.get("", response_model=list[OperatorPublic])
async def list_operators(_owner: OwnerOperator):
    return [operator_public(doc) async for doc in _col().find().sort("created_at", 1)]


@router.post("", response_model=OperatorPublic, status_code=status.HTTP_201_CREATED)
async def create_operator(body: OperatorCreate, request: Request, owner: OwnerOperator):
    login = body.login.lower()
    if await _col().find_one({"login": login}):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Логин «{body.login}» уже занят")
    doc = {
        "login": login,
        "password_hash": hash_password(body.password),
        "name": body.name.strip(),
        "role": body.role,
        "permissions": body.permissions,  # None = все права
        "active": True,
        "created_at": utcnow(),
        "created_by": str(owner["_id"]),
        "last_login_at": None,
    }
    try:
        result = await _col().insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Логин «{body.login}» уже занят")
    doc["_id"] = result.inserted_id

    await write_audit(
        operator=owner, action=audit.ACTION_OPERATOR_CREATE, target_user_id=None,
        new_value={"login": doc["login"], "name": doc["name"], "role": doc["role"]},
        ip=client_ip(request),
    )
    return operator_public(doc)


@router.patch("/{operator_id}", response_model=OperatorPublic)
async def update_operator(operator_id: str, body: OperatorUpdate,
                          request: Request, owner: OwnerOperator):
    oid = _oid(operator_id)
    existing = await _col().find_one({"_id": oid})
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Оператор не найден")

    # An owner cannot demote or deactivate themself — prevents locking everyone out
    if oid == owner["_id"] and (body.active is False or (body.role and body.role != "owner")):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Нельзя отключить или понизить собственную учётную запись")

    updates: dict = {}
    changed_public: dict = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
        changed_public["name"] = updates["name"]
    if body.role is not None:
        updates["role"] = body.role
        changed_public["role"] = body.role
    if body.active is not None:
        updates["active"] = body.active
        changed_public["active"] = body.active
    if body.permissions is not None:
        updates["permissions"] = body.permissions
        changed_public["permissions"] = body.permissions
    if body.password is not None:
        updates["password_hash"] = hash_password(body.password)
        changed_public["password"] = "***changed***"
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет изменений")

    await _col().update_one({"_id": oid}, {"$set": updates})
    updated = await _col().find_one({"_id": oid})

    await write_audit(
        operator=owner, action=audit.ACTION_OPERATOR_UPDATE, target_user_id=None,
        old_value={"login": existing["login"],
                   **{k: existing.get(k) for k in changed_public if k != "password"}},
        new_value={"login": existing["login"], **changed_public},
        ip=client_ip(request),
    )
    return operator_public(updated)
