"""Small helpers shared by aggregation routers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

_UNITS = {"day": "day", "week": "week", "month": "month"}


def bucket_expr(granularity: str, field: str = "$dt") -> dict:
    """$dateTrunc expression for the requested granularity (weeks start Monday)."""
    unit = _UNITS.get(granularity, "day")
    expr = {"date": field, "unit": unit}
    if unit == "week":
        expr["startOfWeek"] = "monday"
    return {"$dateTrunc": expr}


# Real money received in a top-up = amount minus the gifted bonus part.
NET_AMOUNT = {"$subtract": ["$amount", {"$ifNull": ["$bonus", 0]}]}


def r2(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
