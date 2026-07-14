"""Support tickets — the same lifecycle the Telegram bot drives.

Ticket state lives where the bot keeps it: users.info.support
{thread_id, status: pending|open|closed, pending_at}. The panel mirrors every
action to Telegram so both views stay consistent:
  * reply  -> PM to the user + copy into the support-chat forum thread,
              status -> open (оператор подключился), topic title updated
  * close  -> status -> closed, topic title updated, notice in the thread,
              rating keyboard sent to the user (the bot handles rate:{1..5})

Message history is shared via the support_messages collection — the panel
writes its messages there; the bot is patched to do the same (see
docs/bot-integration.md). Old tickets have no backlog, only new messages.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from .. import audit
from ..audit import write_audit
from ..config import get_settings
from ..database import get_db
from ..security import CurrentOperator, client_ip, ensure_permission
from ..telegram import RATING_KEYBOARD, TelegramError, get_telegram
from ..user_service import users_col
from ..utils import jsonable, utcnow

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

TICKET_STATUSES = ("pending", "open", "closed")


def _messages_col():
    return get_db()[get_settings().support_messages_collection]


class TicketReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3500)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ticket_brief(doc: dict) -> dict:
    ud = doc.get("user_data") or {}
    support = ((doc.get("info") or {}).get("support")) or {}
    return {
        "user_id": ud.get("user_id"),
        "username": ud.get("username"),
        "first_name": ud.get("first_name"),
        "status": (support.get("status") or "pending").lower(),
        "thread_id": support.get("thread_id"),
        "pending_at": jsonable(support.get("pending_at")),
    }


async def _find_ticket_user(user_id: int) -> tuple[dict, dict]:
    doc = await users_col().find_one(
        {"user_data.user_id": user_id},
        {"user_data": 1, "info.support": 1},
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Пользователь {user_id} не найден")
    support = ((doc.get("info") or {}).get("support")) or {}
    if not support.get("thread_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "У пользователя нет тикета (он ещё не писал в поддержку)")
    return doc, support


async def _store_message(*, user_id: int, direction: str, text: str,
                         operator_login: str | None = None, source: str = "site",
                         attachment: dict | None = None) -> dict:
    msg = {
        "user_id": user_id,
        "direction": direction,          # user | operator | system
        "text": text,
        "operator_login": operator_login,
        "source": source,                # site | tg
        "attachment": attachment,        # {type, file_id, name?} | None
        "timestamp": utcnow(),
    }
    await _messages_col().insert_one(msg)
    msg.pop("_id", None)
    return msg


# ---------------------------------------------------------------- list

SORT_FIELDS = {
    "pending_at": "info.support.pending_at",
    "status": "info.support.status",
    "user": "user_data.first_name",
}


@router.get("")
async def list_tickets(
    _op: CurrentOperator,
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    sort: str = Query(default="pending_at", max_length=16),
    order: str = Query(default="desc", max_length=4),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    query: dict = {"info.support.thread_id": {"$exists": True}}
    if status_filter:
        if status_filter not in TICKET_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверный статус")
        query["info.support.status"] = status_filter
    if sort not in SORT_FIELDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неверное поле сортировки")
    direction = -1 if order != "asc" else 1

    col = users_col()
    total = await col.count_documents(query)
    cursor = (
        col.find(query, {"user_data": 1, "info.support": 1})
        .sort(SORT_FIELDS[sort], direction)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_ticket_brief(doc) async for doc in cursor]

    # Последние сообщения всех тикетов страницы — ОДНИМ запросом (был N+1,
    # что при удалённой Mongo давало 30+ сетевых кругов на каждое обновление)
    ids = [b["user_id"] for b in items if b["user_id"] is not None]
    if ids:
        pipeline = [
            {"$match": {"user_id": {"$in": ids}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$user_id",
                        "direction": {"$first": "$direction"},
                        "text": {"$first": "$text"},
                        "timestamp": {"$first": "$timestamp"}}},
        ]
        last_map = {d["_id"]: d async for d in _messages_col().aggregate(pipeline)}
        for brief in items:
            last = last_map.get(brief["user_id"])
            if last:
                brief["last_message"] = {
                    "direction": last.get("direction"),
                    "text": (last.get("text") or "")[:120],
                    "timestamp": jsonable(last.get("timestamp")),
                }
    return {"total": total, "page": page, "page_size": page_size, "items": items}


# ---------------------------------------------------------------- attachments

@router.get("/file/{file_id}")
async def ticket_file(file_id: str, _op: CurrentOperator):
    """Проксирует вложение из Telegram (getFile) авторизованному оператору.
    file_id берётся из support_messages.attachment.file_id."""
    import mimetypes
    import re as _re
    from pathlib import PurePosixPath

    from fastapi.responses import Response

    if not _re.fullmatch(r"[A-Za-z0-9_-]{10,200}", file_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Некорректный file_id")
    try:
        content, path = await get_telegram().get_file(file_id)
    except TelegramError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Вложение недоступно: {e.message}")

    mime = mimetypes.guess_type(path)[0]
    if mime is None:
        mime = "audio/ogg" if path.endswith(".oga") else "application/octet-stream"
    return Response(
        content,
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{PurePosixPath(path).name}"',
        },
    )


# ---------------------------------------------------------------- ticket view

async def _ticket_signature(user_id: int) -> str:
    """Дешёвый «отпечаток» тикета: число сообщений + время последнего + статус.
    Изменился отпечаток — значит, есть что показать оператору."""
    col = _messages_col()
    count = await col.count_documents({"user_id": user_id})
    last = await col.find({"user_id": user_id}, {"timestamp": 1}) \
        .sort("timestamp", -1).limit(1).to_list(1)
    last_ts = jsonable(last[0].get("timestamp")) if last else ""
    doc = await users_col().find_one(
        {"user_data.user_id": user_id}, {"info.support.status": 1})
    st = ((((doc or {}).get("info") or {}).get("support")) or {}).get("status") or "pending"
    return f"{count}|{last_ts}|{st}"


@router.get("/{user_id}")
async def ticket_detail(user_id: int, _op: CurrentOperator,
                        limit: int = Query(default=200, ge=1, le=1000)):
    doc, _support = await _find_ticket_user(user_id)
    messages = [
        {k: jsonable(v) for k, v in m.items() if k != "_id"}
        async for m in _messages_col().find({"user_id": user_id})
        .sort("timestamp", -1).limit(limit)
    ]
    messages.reverse()
    return {"ticket": _ticket_brief(doc), "messages": messages,
            "sig": await _ticket_signature(user_id)}


LONGPOLL_MAX_WAIT = 25.0   # меньше типовых proxy_read_timeout (nginx: 60с)
LONGPOLL_STEP = 1.0


@router.get("/{user_id}/updates")
async def ticket_updates(user_id: int, _op: CurrentOperator,
                         sig: str = Query(default="", max_length=200),
                         wait: float = Query(default=LONGPOLL_MAX_WAIT, ge=0, le=LONGPOLL_MAX_WAIT)):
    """Long-poll: держим запрос открытым, пока в тикете не появится новое
    сообщение или не сменится статус (в т.ч. записанные ботом из Telegram) —
    чат на сайте обновляется мгновенно, без периодической перезагрузки."""
    await _find_ticket_user(user_id)  # 404, если тикета нет
    loop = asyncio.get_event_loop()
    deadline = loop.time() + wait
    while True:
        current = await _ticket_signature(user_id)
        if current != sig or loop.time() >= deadline:
            break
        await asyncio.sleep(min(LONGPOLL_STEP, max(0.0, deadline - loop.time())))
    if current == sig:
        return {"changed": False, "sig": current}
    # detail сам пересчитает sig — он свежее, чем current (сообщение могло
    # прийти между проверкой отпечатка и выборкой диалога)
    detail = await ticket_detail(user_id, _op, limit=200)
    return {"changed": True, **detail}


# ---------------------------------------------------------------- reply

@router.post("/{user_id}/reply")
async def reply_ticket(user_id: int, body: TicketReplyRequest,
                       request: Request, operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    doc, support = await _find_ticket_user(user_id)
    thread_id = support["thread_id"]
    old_status = (support.get("status") or "pending").lower()
    text = body.text.strip()

    tg = get_telegram()

    def _is_parse_error(e: TelegramError) -> bool:
        # «can't parse entities…» — текст содержит < >, не являющиеся разметкой
        return "parse" in (e.message or "").lower()

    # 1) пользователю в ЛС — с HTML-разметкой (быстрые ответы форматируются
    #    тегами как в боте); если текст не является валидным HTML — шлём как есть
    try:
        try:
            await tg.send_to_user(user_id, text)
        except TelegramError as e:
            if not _is_parse_error(e):
                raise
            await tg.send_to_user(user_id, _esc(text))
    except TelegramError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Сообщение НЕ отправлено пользователю: {e.message}")

    # 2) зеркало в тред — чтобы операторы в TG видели переписку с сайта
    mirror_prefix = f"💻 <b>Ответ с сайта</b> — {_esc(operator.get('name') or operator['login'])}:\n\n"
    try:
        try:
            await tg.send_to_thread(thread_id, mirror_prefix + text)
        except TelegramError as e:
            if not _is_parse_error(e):
                raise
            await tg.send_to_thread(thread_id, mirror_prefix + _esc(text))
    except TelegramError as e:
        # юзеру уже ушло; тред не синкнулся — фиксируем, но не откатываем
        await _store_message(user_id=user_id, direction="system",
                             text=f"⚠️ Не удалось продублировать в тред: {e.message}")

    # 3) оператор подключился -> open (как выбор «оператор» в боте)
    if old_status != "open":
        await users_col().update_one(
            {"user_data.user_id": user_id},
            {"$set": {"info.support.status": "open"}},
        )
        await tg.set_thread_status_title(thread_id, user_id, "open")

    msg = await _store_message(user_id=user_id, direction="operator", text=text,
                               operator_login=operator["login"])
    await write_audit(
        operator=operator, action=audit.ACTION_TICKET_REPLY, target_user_id=user_id,
        old_value={"status": old_status}, new_value={"status": "open"},
        reason=None, ip=client_ip(request),
        extra={"text": text[:500]},
    )
    return {"ok": True, "status": "open", "message": jsonable(msg)}


# ---------------------------------------------------------------- AI draft

@router.post("/{user_id}/suggest")
async def suggest_ai_reply(user_id: int, operator: CurrentOperator):
    """Черновик ответа от ИИ. Ничего никуда не отправляет — оператор
    просматривает, правит и шлёт через обычный reply (который аудируется)."""
    ensure_permission(operator, "tickets")
    await _find_ticket_user(user_id)  # 404, если тикета нет
    from ..ai import AIError, suggest_reply
    try:
        suggestion = await suggest_reply(user_id)
    except AIError as e:
        raise HTTPException(e.status_code, e.message)
    except Exception as e:  # что угодно неожиданное — читаемо оператору, трейс в лог
        import logging
        logging.getLogger(__name__).exception("AI suggest failed for user %s", user_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"ИИ-помощник: внутренняя ошибка ({type(e).__name__}: {str(e)[:200]})")
    return {"suggestion": suggestion}


# ---------------------------------------------------------------- reply with photo

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # лимит Bot API для sendPhoto


@router.post("/{user_id}/photo")
async def reply_photo(user_id: int, request: Request, operator: CurrentOperator,
                      file: UploadFile = File(...),
                      caption: str = Form(default="", max_length=1000)):
    """Отправить пользователю фото (+подпись). Как и текстовый ответ:
    ЛС пользователю, зеркало в тред, статус -> open."""
    ensure_permission(operator, "tickets")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Можно прикреплять только изображения")
    photo = await file.read()
    if len(photo) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Фото больше 10 МБ — Telegram не примет")
    if not photo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Пустой файл")
    caption = caption.strip()

    doc, support = await _find_ticket_user(user_id)
    thread_id = support["thread_id"]
    old_status = (support.get("status") or "pending").lower()
    filename = file.filename or "photo.jpg"

    tg = get_telegram()
    try:
        result = await tg.send_photo_to_user(user_id, photo, filename,
                                             caption=_esc(caption) if caption else None)
    except TelegramError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Фото НЕ отправлено пользователю: {e.message}")
    sizes = (result or {}).get("photo") or []
    file_id = sizes[-1].get("file_id") if sizes else None

    op_name = _esc(operator.get("name") or operator["login"])
    try:
        await tg.send_photo_to_thread(
            thread_id, photo, filename,
            caption=f"💻 <b>Фото с сайта</b> — {op_name}" + (f":\n{_esc(caption)}" if caption else ""))
    except TelegramError as e:
        await _store_message(user_id=user_id, direction="system",
                             text=f"⚠️ Не удалось продублировать фото в тред: {e.message}")

    if old_status != "open":
        await users_col().update_one(
            {"user_data.user_id": user_id},
            {"$set": {"info.support.status": "open"}},
        )
        await tg.set_thread_status_title(thread_id, user_id, "open")

    msg = await _store_message(
        user_id=user_id, direction="operator", text=caption,
        operator_login=operator["login"],
        attachment={"type": "photo", "file_id": file_id} if file_id else None,
    )
    await write_audit(
        operator=operator, action=audit.ACTION_TICKET_REPLY, target_user_id=user_id,
        old_value={"status": old_status}, new_value={"status": "open"},
        reason=None, ip=client_ip(request),
        extra={"photo": True, "caption": caption[:200]},
    )
    return {"ok": True, "status": "open", "message": jsonable(msg)}


# ---------------------------------------------------------------- close

@router.post("/{user_id}/close")
async def close_ticket(user_id: int, request: Request, operator: CurrentOperator):
    ensure_permission(operator, "tickets")
    doc, support = await _find_ticket_user(user_id)
    thread_id = support["thread_id"]
    old_status = (support.get("status") or "pending").lower()
    if old_status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Тикет уже закрыт")

    await users_col().update_one(
        {"user_data.user_id": user_id},
        {"$set": {"info.support.status": "closed"}},
    )

    tg = get_telegram()
    await tg.set_thread_status_title(thread_id, user_id, "closed")
    try:
        await tg.send_to_thread(
            thread_id,
            f"🔴 <b>Тикет закрыт оператором с сайта</b> "
            f"({_esc(operator.get('name') or operator['login'])})",
        )
    except TelegramError:
        pass
    # оценка пользователю — те же кнопки rate:{1..5}, их обрабатывает бот
    user_notified = True
    try:
        await tg.send_to_user(
            user_id,
            "✅ <b>Спасибо за обращение!</b>\n"
            "Ваш тикет закрыт. Пожалуйста, оцените работу поддержки:",
            reply_markup=RATING_KEYBOARD,
        )
    except TelegramError:
        user_notified = False

    await _store_message(user_id=user_id, direction="system",
                         text=f"Тикет закрыт оператором {operator['login']} (сайт)",
                         operator_login=operator["login"])
    await write_audit(
        operator=operator, action=audit.ACTION_TICKET_CLOSE, target_user_id=user_id,
        old_value={"status": old_status}, new_value={"status": "closed"},
        reason=None, ip=client_ip(request),
        extra={"user_notified": user_notified},
    )
    return {"ok": True, "status": "closed", "user_notified": user_notified}
