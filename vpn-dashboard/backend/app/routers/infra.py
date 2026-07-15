"""Infrastructure router: Remnawave node status and activity heatmaps.

``GET /infra/nodes`` is live (never cached — the client caches for 15 seconds
internally) and proxies :class:`app.remnawave.RemnawaveClient`; a panel
failure yields ``{"available": false}`` instead of a 500. The heatmap
endpoints read the precomputed ``activity_stats`` document written by the ETL
(``_id="heatmap"``) and are cached for 5 minutes. A missing document produces
empty/zero-filled shapes.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter

from ..agg import as_utc, iso
from ..cache import cached
from ..db import ACTIVITY, get_db
from ..remnawave import get_remnawave

log = logging.getLogger("app.infra")
router = APIRouter(prefix="/infra", tags=["infra"])

_HEATMAP_ID = "heatmap"


def _int(value: Any) -> Optional[int]:
    """Best-effort int coercion; None when the value is not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


async def _heatmap_cells() -> tuple[list[dict[str, Any]], Optional[str]]:
    """Sanitized cells + computed_at from the ETL heatmap document."""
    db = get_db()
    doc = await db[ACTIVITY].find_one({"_id": _HEATMAP_ID})
    if not isinstance(doc, dict):
        return [], None

    cells: list[dict[str, Any]] = []
    raw_cells = doc.get("cells")
    for cell in raw_cells if isinstance(raw_cells, list) else []:
        if not isinstance(cell, dict):
            continue
        dow = _int(cell.get("dow"))
        hour = _int(cell.get("hour"))
        count = _int(cell.get("count"))
        if dow is None or hour is None or not (0 <= dow <= 6 and 0 <= hour <= 23):
            continue
        cells.append({"dow": dow, "hour": hour, "count": count or 0})
    return cells, iso(as_utc(doc.get("computed_at")))


# ---------------------------------------------------------------------------
# GET /infra/nodes — live panel status, NOT cached
# ---------------------------------------------------------------------------

@router.get("/nodes")
async def nodes() -> dict[str, Any]:
    """Remnawave node list, total online users and realtime bandwidth."""
    client = get_remnawave()

    node_list: Optional[list[dict[str, Any]]] = None
    try:
        node_list = await client.get_nodes()
    except Exception:
        log.warning("remnawave get_nodes failed", exc_info=True)
    if node_list is None:
        return {"available": False, "nodes": [], "online_total": 0,
                "realtime": None}

    realtime: Optional[list[dict[str, Any]]] = None
    try:
        realtime = await client.get_realtime_usage()
    except Exception:
        log.warning("remnawave get_realtime_usage failed", exc_info=True)

    online_total = sum(_int(n.get("users_online")) or 0 for n in node_list)
    return {"available": True, "nodes": node_list,
            "online_total": online_total, "realtime": realtime}


# ---------------------------------------------------------------------------
# GET /infra/heatmap — cached 300s
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="infra:heatmap")
async def _heatmap() -> dict[str, Any]:
    cells, computed_at = await _heatmap_cells()
    return {"cells": cells, "computed_at": computed_at}


@router.get("/heatmap")
async def heatmap() -> dict:
    """Hour x weekday activity heatmap precomputed by the ETL."""
    return await _heatmap()


# ---------------------------------------------------------------------------
# GET /infra/peak-hours — cached 300s
# ---------------------------------------------------------------------------

@cached(ttl=300, prefix="infra:peak-hours")
async def _peak_hours() -> dict[str, Any]:
    cells, _ = await _heatmap_cells()
    by_hour = [0] * 24
    for cell in cells:
        by_hour[cell["hour"]] += cell["count"]
    return {"hours": [{"hour": hour, "count": count}
                      for hour, count in enumerate(by_hour)]}


@router.get("/peak-hours")
async def peak_hours() -> dict:
    """Activity per hour of day (heatmap cells summed over weekdays)."""
    return await _peak_hours()
