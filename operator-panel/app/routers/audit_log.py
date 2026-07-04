"""Owner-only: read the audit log. There is deliberately no write/update/delete API."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..audit import ALL_ACTIONS
from ..config import get_settings
from ..database import get_db
from ..security import OwnerOperator
from ..utils import jsonable, parse_any_ts

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/actions")
async def action_types(_op: OwnerOperator):
    return {"actions": ALL_ACTIONS}


@router.get("")
async def audit_log(
    _op: OwnerOperator,
    operator_login: str | None = Query(default=None, max_length=64),
    target_user_id: int | None = None,
    action: str | None = Query(default=None, max_length=64),
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    query: dict = {}
    if operator_login:
        query["operator_login"] = operator_login
    if target_user_id is not None:
        query["target_user_id"] = target_user_id
    if action:
        query["action"] = action

    ts: dict = {}
    dt_from = parse_any_ts(date_from)
    dt_to = parse_any_ts(date_to)
    if dt_from:
        ts["$gte"] = dt_from
    if dt_to:
        # date-only "to" bound includes the whole day
        from datetime import timedelta
        if date_to and len(date_to.strip()) <= 10:
            dt_to = dt_to + timedelta(days=1)
        ts["$lt"] = dt_to
    if ts:
        query["timestamp"] = ts

    col = get_db()[get_settings().audit_collection]
    total = await col.count_documents(query)
    cursor = (
        col.find(query)
        .sort("timestamp", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = [jsonable({**doc, "id": str(doc.pop("_id"))}) async for doc in cursor]
    return {"total": total, "page": page, "page_size": page_size, "items": items}
