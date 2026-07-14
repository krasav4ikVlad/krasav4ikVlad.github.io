"""Личные быстрые ответы оператора.

В отличие от общих (/api/quick-replies), которые живут в коллекции бота и
становятся пунктами меню самопомощи для ВСЕХ пользователей, личные хранятся
в отдельной коллекции панели `operator_quick_replies` и видны только своему
владельцу. Каждый оператор ведёт свой набор сам: заготовки под свой стиль,
черновики, ответы на частые лично у него вопросы.

На бота и ИИ-FAQ не влияют. Аудит не пишется — это личные заметки.
"""
from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..database import get_db
from ..security import CurrentOperator, ensure_permission
from ..utils import utcnow

router = APIRouter(prefix="/api/my-quick-replies", tags=["my-quick-replies"])

MAX_PER_OPERATOR = 200


def _col():
    return get_db()["operator_quick_replies"]


def _pub(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title") or "",
        "text": doc.get("text") or "",
        "order": doc.get("order", 0),
    }


def _oid(item_id: str) -> ObjectId:
    try:
        return ObjectId(item_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")


class MyQuickReplyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=3500)


class MyQuickReplyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=3500)
    order: int | None = Field(default=None, ge=0, le=10_000)


@router.get("")
async def list_my_quick_replies(operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    items = [_pub(d) async for d in
             _col().find({"operator_login": operator["login"]}).sort("order", 1)]
    return {"items": items}


@router.post("")
async def create_my_quick_reply(body: MyQuickReplyCreate, operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    title, text = body.title.strip(), body.text.strip()
    if not title or not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Заполните название и текст")
    if await _col().count_documents({"operator_login": operator["login"]}) >= MAX_PER_OPERATOR:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Не больше {MAX_PER_OPERATOR} личных ответов")
    last = await _col().find({"operator_login": operator["login"]},
                             {"order": 1}).sort("order", -1).limit(1).to_list(1)
    doc = {
        "operator_login": operator["login"],
        "title": title, "text": text,
        "order": (last[0].get("order", 0) if last else 0) + 1,
        "created_at": utcnow(),
    }
    res = await _col().insert_one(doc)
    return _pub({**doc, "_id": res.inserted_id})


@router.patch("/{item_id}")
async def update_my_quick_reply(item_id: str, body: MyQuickReplyUpdate,
                                operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    oid = _oid(item_id)
    # фильтр по логину: чужой личный ответ не найдётся и не изменится
    doc = await _col().find_one({"_id": oid, "operator_login": operator["login"]})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip()
    if body.text is not None:
        updates["text"] = body.text.strip()
    if body.order is not None:
        updates["order"] = body.order
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет изменений")
    updates["updated_at"] = utcnow()
    await _col().update_one({"_id": oid}, {"$set": updates})
    return _pub(await _col().find_one({"_id": oid}))


@router.delete("/{item_id}")
async def delete_my_quick_reply(item_id: str, operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    oid = _oid(item_id)
    res = await _col().delete_one({"_id": oid, "operator_login": operator["login"]})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Быстрый ответ не найден")
    return {"ok": True}
