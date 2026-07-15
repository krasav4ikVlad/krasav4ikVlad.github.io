"""Shared router dependencies: period filter parsing and admin guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import HTTPException, Query

from ..auth import require_admin  # re-export for routers
from ..db import get_db  # re-export for routers

__all__ = ["Period", "get_period", "require_admin", "get_db", "Granularity"]

Granularity = Literal["day", "week", "month"]


@dataclass
class Period:
    """Global period filter. ``from_dt`` may be None (= all time)."""

    from_dt: Optional[datetime]
    to_dt: datetime

    def match(self, field: str = "dt") -> dict:
        cond: dict = {"$lt": self.to_dt}
        if self.from_dt is not None:
            cond["$gte"] = self.from_dt
        return {field: cond}

    @property
    def days(self) -> float:
        if self.from_dt is None:
            return 3650.0
        return max((self.to_dt - self.from_dt).total_seconds() / 86400, 1 / 24)

    def previous(self) -> "Period":
        """The adjacent preceding window of the same length (for comparisons)."""
        if self.from_dt is None:
            return Period(None, self.to_dt)
        span = self.to_dt - self.from_dt
        return Period(self.from_dt - span, self.from_dt)


def _parse(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, f"Invalid {name!r} datetime: {value!r}")


def get_period(
    from_: Optional[str] = Query(None, alias="from",
                                 description="ISO datetime, omit for all time"),
    to: Optional[str] = Query(None, description="ISO datetime, default now"),
) -> Period:
    to_dt = _parse(to, "to") if to else datetime.now(timezone.utc)
    from_dt = _parse(from_, "from") if from_ else None
    if from_dt and from_dt >= to_dt:
        raise HTTPException(422, "'from' must be before 'to'")
    return Period(from_dt, to_dt)
