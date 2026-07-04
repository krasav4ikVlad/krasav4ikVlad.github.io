#!/usr/bin/env python3
"""Repair: convert string vpn.expireAt / vpn.bypass_expireAt back to native datetime.

Early panel versions wrote these fields as ISO strings, while the bot stores and
expects BSON Date (it calls .strftime on the value). The bot itself never writes
strings there, so every string-typed value was written by the panel and is safe
to convert.

Run from the project root (so .env is picked up):
    cd /opt/operator-panel && venv/bin/python scripts/fix_expire_types.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.utils import parse_any_ts  # noqa: E402

FIELDS = ["vpn.expireAt", "vpn.bypass_expireAt"]


async def main() -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=5000)
    col = client[settings.mongo_db][settings.users_collection]

    query = {"$or": [{f: {"$type": "string"}} for f in FIELDS]}
    fixed = 0
    async for doc in col.find(query, {"user_data.user_id": 1, "vpn.expireAt": 1,
                                      "vpn.bypass_expireAt": 1}):
        vpn = doc.get("vpn") or {}
        updates = {}
        for field in FIELDS:
            value = vpn.get(field.split(".", 1)[1])
            if isinstance(value, str):
                dt = parse_any_ts(value)
                if dt is None:
                    print(f"  ! user {doc['user_data']['user_id']}: не удалось разобрать {field}={value!r}, пропущен")
                    continue
                updates[field] = dt
        if updates:
            await col.update_one({"_id": doc["_id"]}, {"$set": updates})
            fixed += 1
            print(f"  fixed user {doc['user_data']['user_id']}: {list(updates)}")

    print(f"Готово, исправлено документов: {fixed}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
