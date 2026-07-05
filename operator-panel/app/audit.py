"""Append-only audit log for operator actions.

Every mutating endpoint calls write_audit(). There are NO update/delete code paths
for this collection anywhere in the application — operators physically cannot touch
it through the panel. For defense in depth, run the panel under a MongoDB user that
has only `insert`/`find` privileges on `operator_logs` (see README).
"""
from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .database import get_db
from .utils import utcnow

log = logging.getLogger(__name__)

# Action types (used for audit filtering; keep in sync with the frontend dictionary)
ACTION_LOGIN = "login"
ACTION_BALANCE_CHANGE = "balance_change"
ACTION_SUB_EXPIRE = "subscription_expire_change"
ACTION_DEVICE_LIMIT = "device_limit_change"
ACTION_BYPASS_UPDATE = "bypass_update"
ACTION_DEVICE_RESET = "device_reset"
ACTION_EMAIL_CHANGE = "email_change"
ACTION_GIFT = "gift"
ACTION_OPERATOR_CREATE = "operator_create"
ACTION_OPERATOR_UPDATE = "operator_update"
ACTION_OPERATOR_PWD_RESET = "operator_password_reset"
ACTION_PASSWORD_CHANGE = "password_change"
ACTION_TICKET_REPLY = "ticket_reply"
ACTION_TICKET_CLOSE = "ticket_close"

ALL_ACTIONS = [
    ACTION_LOGIN, ACTION_BALANCE_CHANGE, ACTION_SUB_EXPIRE, ACTION_DEVICE_LIMIT,
    ACTION_BYPASS_UPDATE, ACTION_DEVICE_RESET, ACTION_EMAIL_CHANGE, ACTION_GIFT,
    ACTION_OPERATOR_CREATE, ACTION_OPERATOR_UPDATE,
    ACTION_OPERATOR_PWD_RESET, ACTION_PASSWORD_CHANGE,
    ACTION_TICKET_REPLY, ACTION_TICKET_CLOSE,
]


async def write_audit(
    *,
    operator: dict,
    action: str,
    target_user_id: int | None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    ip: str = "unknown",
    remnawave_synced: bool | None = None,
    remnawave_error: str | None = None,
    extra: dict | None = None,
) -> None:
    """Insert an audit record. Never raises: a failed audit write is logged loudly
    but must not roll back an already-applied user change (the change itself is the
    source of truth; ops should alert on these log lines)."""
    doc = {
        "operator_id": str(operator["_id"]),
        "operator_login": operator.get("login", "?"),
        "operator_name": operator.get("name", ""),
        "action": action,
        "target_user_id": target_user_id,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "ip": ip,
        "remnawave_synced": remnawave_synced,
        "remnawave_error": remnawave_error,
        "timestamp": utcnow(),
    }
    if extra:
        doc["extra"] = extra
    try:
        settings = get_settings()
        await get_db()[settings.audit_collection].insert_one(doc)
    except Exception:
        log.exception(
            "AUDIT WRITE FAILED action=%s operator=%s target=%s — investigate immediately",
            action, operator.get("login"), target_user_id,
        )
