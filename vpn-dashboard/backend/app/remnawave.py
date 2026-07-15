"""Remnawave panel REST client (nodes, online users, traffic).

Panel versions expose slightly different paths and casing, so the client
tries known path candidates, remembers the working one, unwraps
``{"response": ...}`` envelopes and tolerates camelCase/snake_case keys.
Every method returns normalized dicts or ``None`` when the panel is
unreachable — the API layer turns that into ``{"available": false}``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from .config import get_settings

log = logging.getLogger("app.remnawave")

_NODES_PATHS = ["/api/nodes", "/api/nodes/get-all"]
_STATS_PATHS = ["/api/system/stats", "/api/system/health"]
_REALTIME_PATHS = ["/api/nodes/usage/realtime", "/api/bandwidth/realtime"]


def _g(d: dict, *names: str, default: Any = None) -> Any:
    """Get the first present key, trying snake_case and camelCase."""
    for n in names:
        if n in d:
            return d[n]
        camel = "".join(w.capitalize() if i else w
                        for i, w in enumerate(n.split("_")))
        if camel in d:
            return d[camel]
    return default


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("response", "data", "result"):
            if key in payload and isinstance(payload[key], (list, dict)):
                return payload[key]
    return payload


class RemnawaveClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.remnawave_api_url.rstrip("/")
        self.enabled = bool(self.base_url)
        self._client: httpx.AsyncClient | None = None
        self._working_paths: dict[str, str] = {}
        self._cache: dict[str, tuple[float, Any]] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            s = get_settings()
            headers = {"Accept": "application/json"}
            if s.remnawave_token:
                headers["Authorization"] = f"Bearer {s.remnawave_token}"
                # some deployments sit behind a proxy expecting this header
                headers["X-Api-Key"] = s.remnawave_token
            self._client = httpx.AsyncClient(
                base_url=self.base_url, headers=headers, timeout=8.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, group: str, candidates: list[str],
                   cache_ttl: float = 15.0) -> Any:
        if not self.enabled:
            return None
        cached = self._cache.get(group)
        if cached and time.monotonic() - cached[0] < cache_ttl:
            return cached[1]

        paths = candidates
        if group in self._working_paths:
            paths = [self._working_paths[group]]
        for path in paths:
            try:
                resp = await self._http().get(path)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = _unwrap(resp.json())
                self._working_paths[group] = path
                self._cache[group] = (time.monotonic(), data)
                return data
            except httpx.HTTPError as e:
                log.warning("remnawave request failed",
                            extra={"path": path, "error": str(e)})
        self._working_paths.pop(group, None)
        return None

    async def get_nodes(self) -> Optional[list[dict]]:
        data = await self._get("nodes", _NODES_PATHS)
        if data is None:
            return None
        items = data if isinstance(data, list) else _g(data, "nodes", default=[])
        nodes = []
        for n in items if isinstance(items, list) else []:
            if not isinstance(n, dict):
                continue
            nodes.append({
                "uuid": _g(n, "uuid", "id"),
                "name": _g(n, "name", default="node"),
                "country": _g(n, "country_code", "country"),
                "address": _g(n, "address", "host"),
                "is_online": bool(_g(n, "is_node_online", "is_online",
                                     "is_connected", default=False)),
                "users_online": int(_g(n, "users_online", "online_users",
                                       default=0) or 0),
                "traffic_used_bytes": float(
                    _g(n, "traffic_used_bytes", "total_traffic_bytes",
                       default=0) or 0),
                "traffic_limit_bytes": float(
                    _g(n, "traffic_limit_bytes", default=0) or 0),
                "cpu_percent": _g(n, "cpu_usage", "cpu_percent"),
                "mem_percent": _g(n, "mem_usage", "memory_percent"),
            })
        return nodes

    async def get_online_count(self) -> Optional[int]:
        stats = await self._get("stats", _STATS_PATHS)
        if isinstance(stats, dict):
            online = _g(stats, "online_now")
            if online is None and isinstance(_g(stats, "online_stats"), dict):
                online = _g(_g(stats, "online_stats"), "online_now", "online")
            if online is None and isinstance(_g(stats, "users"), dict):
                online = _g(_g(stats, "users"), "online_now", "online")
            if online is not None:
                try:
                    return int(online)
                except (TypeError, ValueError):
                    return None
        # fall back to summing per-node online counters
        nodes = await self.get_nodes()
        if nodes is not None:
            return sum(n["users_online"] for n in nodes)
        return None

    async def get_realtime_usage(self) -> Optional[list[dict]]:
        data = await self._get("realtime", _REALTIME_PATHS, cache_ttl=10.0)
        if data is None:
            return None
        items = data if isinstance(data, list) else []
        out = []
        for u in items:
            if not isinstance(u, dict):
                continue
            out.append({
                "node_uuid": _g(u, "node_uuid", "uuid", "id"),
                "node_name": _g(u, "node_name", "name"),
                "download_bytes": float(_g(u, "download_bytes", "rx", default=0) or 0),
                "upload_bytes": float(_g(u, "upload_bytes", "tx", default=0) or 0),
                "download_speed_bps": float(
                    _g(u, "download_speed_bps", "rx_speed", default=0) or 0),
                "upload_speed_bps": float(
                    _g(u, "upload_speed_bps", "tx_speed", default=0) or 0),
            })
        return out


_client: RemnawaveClient | None = None


def get_remnawave() -> RemnawaveClient:
    global _client
    if _client is None:
        _client = RemnawaveClient()
    return _client
