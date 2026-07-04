"""Helpers: date parsing for the bot's mixed timestamp formats, safe serialization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

# The bot stores dates in two formats:
#   "16.09.2024 03:16:04"          (user_data.date_joined, logs[], logs_balance[])
#   "2027-02-11T00:00:00.000Z"     (vpn.expireAt, vpn.createdAt, bypass_expireAt)
BOT_TS_FORMAT = "%d.%m.%Y %H:%M:%S"

GB = 1024 ** 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_any_ts(value: Any) -> datetime | None:
    """Parse either bot format or ISO 8601. Returns tz-aware UTC datetime or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        return datetime.strptime(value, BOT_TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def to_iso_z(dt: datetime) -> str:
    """Serialize to the ISO format Remnawave / the bot use: 2027-02-11T00:00:00.000Z"""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def bot_ts_now() -> str:
    """Timestamp in the bot's own format for entries we append to user history."""
    return utcnow().strftime(BOT_TS_FORMAT)


def shift_expire(current: Any, days: int) -> datetime:
    """New expiry = max(current, now) + days.

    Extending an expired subscription starts from *now*, not from the stale date;
    shortening applies to whatever the effective base is.
    """
    now = utcnow()
    base = parse_any_ts(current) or now
    if days >= 0 and base < now:
        base = now
    return base + timedelta(days=days)


def jsonable(value: Any) -> Any:
    """Recursively convert Mongo documents to JSON-safe structures."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return to_iso_z(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value
