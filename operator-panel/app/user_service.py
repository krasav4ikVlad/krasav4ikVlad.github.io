"""Shared helpers for reading/locating user documents in RS_2.users."""
from __future__ import annotations

import re

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorCollection

from .config import get_settings
from .database import get_db

# Projection for search results / user card header — never pull full logs into search
BRIEF_PROJECTION = {
    "user_data": 1,
    "info.balance": 1,
    "info.email": 1,
    "vpn.expireAt": 1,
    "vpn.hwidDeviceLimit": 1,
    "growth.segment": 1,
    "role": 1,
}


def users_col() -> AsyncIOMotorCollection:
    return get_db()[get_settings().users_collection]


async def find_user_or_404(user_id: int, projection: dict | None = None) -> dict:
    doc = await users_col().find_one({"user_data.user_id": user_id}, projection)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Пользователь {user_id} не найден")
    return doc


async def search_users(query: str, limit: int = 20) -> list[dict]:
    """Search by user_id (exact), username (case-insensitive, @ optional) or email."""
    query = query.strip()
    if not query:
        return []
    filters: list[dict] = []

    if query.isdigit():
        filters.append({"user_data.user_id": int(query)})

    term = query.lstrip("@")
    if term:
        escaped = re.escape(term)
        # exact case-insensitive match first; prefix match as fallback
        filters.append({"user_data.username": {"$regex": f"^{escaped}$", "$options": "i"}})
        if len(term) >= 3:
            filters.append({"user_data.username": {"$regex": f"^{escaped}", "$options": "i"}})

    if "@" in query and not query.startswith("@"):
        filters.append({"info.email": {"$regex": f"^{re.escape(query)}$", "$options": "i"}})

    if not filters:
        return []

    cursor = users_col().find({"$or": filters}, BRIEF_PROJECTION).limit(limit)
    results: list[dict] = []
    seen: set[int] = set()
    async for doc in cursor:
        uid = (doc.get("user_data") or {}).get("user_id")
        if uid in seen:
            continue
        seen.add(uid)
        results.append(doc)
    return results


def brief_view(doc: dict) -> dict:
    ud = doc.get("user_data") or {}
    info = doc.get("info") or {}
    vpn = doc.get("vpn") or {}
    return {
        "user_id": ud.get("user_id"),
        "username": ud.get("username"),
        "first_name": ud.get("first_name"),
        "date_joined": ud.get("date_joined"),
        "balance": info.get("balance", 0),
        "email": info.get("email"),
        "expire_at": vpn.get("expireAt"),
        "device_limit": vpn.get("hwidDeviceLimit"),
        "segment": (doc.get("growth") or {}).get("segment"),
        "role": doc.get("role"),
    }
