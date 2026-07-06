"""Общие быстрые ответы поддержки.

Та же коллекция support_quick_replies, из которой бот строит меню быстрых
ответов и берёт тексты инструкций: {key, title, text, order, active}.
Всё, что операторы добавляют/правят здесь, сразу видно и в боте, и в
ИИ-помощнике (он использует эти тексты как FAQ), и наоборот — тексты бота
доступны на сайте одним кликом.

Управлять могут все операторы с правом «Тикеты» (быстрые ответы — часть
работы с тикетами); каждое изменение пишется в аудит-лог.
"""
from __future__ import annotations

import re
import secrets

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..audit import write_audit
from ..database import get_db
from ..security import CurrentOperator, client_ip, ensure_permission

router = APIRouter(prefix="/api/quick-replies", tags=["quick-replies"])

KEY_RE = re.compile(r"^[a-z0-9_]{2,48}$")


def _col():
    return get_db()["support_quick_replies"]


def _pub(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "key": doc.get("key"),
        "title": doc.get("title") or "",
        "text": doc.get("text") or "",
        "order": doc.get("order", 0),
        "active": bool(doc.get("active", True)),
    }


def _oid(qr_id: str) -> ObjectId:
    try:
        return ObjectId(qr_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")


class QuickReplyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=3500)


class QuickReplyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=3500)
    active: bool | None = None
    order: int | None = Field(default=None, ge=0, le=10_000)


@router.get("")
async def list_quick_replies(operator: CurrentOperator, all: bool = False):
    """Список быстрых ответов. all=true — включая выключенные (для управления)."""
    query = {} if all else {"active": True}
    items = [_pub(d) async for d in _col().find(query).sort("order", 1)]
    return {"items": items}


@router.post("")
async def create_quick_reply(body: QuickReplyCreate, request: Request,
                             operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    title, text = body.title.strip(), body.text.strip()
    if not title or not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Заполните название и текст")

    # key нужен боту для callback-кнопок — генерируем короткий и уникальный
    key = f"qr_{secrets.token_hex(4)}"
    while await _col().find_one({"key": key}, {"_id": 1}):
        key = f"qr_{secrets.token_hex(4)}"

    last = await _col().find({}, {"order": 1}).sort("order", -1).limit(1).to_list(1)
    order = (last[0].get("order", 0) if last else 0) + 1

    from ..utils import utcnow
    doc = {
        "key": key, "title": title, "text": text,
        "order": order, "active": True,
        "created_by": operator["login"], "created_at": utcnow(),
    }
    res = await _col().insert_one(doc)
    await write_audit(
        operator=operator, action=audit.ACTION_QUICK_REPLY_CREATE, target_user_id=None,
        old_value=None, new_value={"key": key, "title": title},
        reason=None, ip=client_ip(request), extra={"text": text[:300]},
    )
    return _pub({**doc, "_id": res.inserted_id})


@router.patch("/{qr_id}")
async def update_quick_reply(qr_id: str, body: QuickReplyUpdate, request: Request,
                             operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    oid = _oid(qr_id)
    doc = await _col().find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")

    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip()
    if body.text is not None:
        updates["text"] = body.text.strip()
    if body.active is not None:
        updates["active"] = body.active
    if body.order is not None:
        updates["order"] = body.order
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет изменений")

    from ..utils import utcnow
    updates["updated_by"] = operator["login"]
    updates["updated_at"] = utcnow()
    await _col().update_one({"_id": oid}, {"$set": updates})

    await write_audit(
        operator=operator, action=audit.ACTION_QUICK_REPLY_UPDATE, target_user_id=None,
        old_value={"key": doc.get("key"), "title": doc.get("title"),
                   "text": (doc.get("text") or "")[:300], "active": doc.get("active", True)},
        new_value={k: (v[:300] if isinstance(v, str) else v)
                   for k, v in updates.items() if k not in ("updated_by", "updated_at")},
        reason=None, ip=client_ip(request),
    )
    fresh = await _col().find_one({"_id": oid})
    return _pub(fresh)


@router.delete("/{qr_id}")
async def delete_quick_reply(qr_id: str, request: Request, operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    oid = _oid(qr_id)
    doc = await _col().find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")
    await _col().delete_one({"_id": oid})
    await write_audit(
        operator=operator, action=audit.ACTION_QUICK_REPLY_DELETE, target_user_id=None,
        old_value={"key": doc.get("key"), "title": doc.get("title"),
                   "text": (doc.get("text") or "")[:300]},
        new_value=None, reason=None, ip=client_ip(request),
    )
    return {"ok": True}
