#!/usr/bin/env python3
"""Bootstrap / manage operator accounts from the server console.

Usage:
    python scripts/create_operator.py --login owner --name "Владелец" --role owner
    python scripts/create_operator.py --login op1 --name "Оператор 1" --role operator

The password is prompted interactively (not passed via argv — keeps it out of shell history).
Reads MONGO_URL / MONGO_DB from .env in the project root (same as the app).
"""
import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.utils import utcnow  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create an operator account")
    parser.add_argument("--login", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=["operator", "owner"], default="operator")
    args = parser.parse_args()

    password = getpass.getpass("Пароль (мин. 8 символов): ")
    if len(password) < 8:
        sys.exit("Пароль слишком короткий")
    if password != getpass.getpass("Повторите пароль: "):
        sys.exit("Пароли не совпадают")

    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_url)
    col = client[settings.mongo_db][settings.operators_collection]

    if await col.find_one({"login": args.login.lower()}):
        sys.exit(f"Логин «{args.login}» уже существует")

    await col.insert_one({
        "login": args.login.lower(),
        "password_hash": hash_password(password),
        "name": args.name,
        "role": args.role,
        "active": True,
        "created_at": utcnow(),
        "created_by": "console",
        "last_login_at": None,
    })
    print(f"Создан {args.role}: {args.login}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
