#!/usr/bin/env python3
"""Bootstrap / manage operator accounts from the server console.

Usage:
    python scripts/create_operator.py --login owner --name "Владелец" --role owner
    python scripts/create_operator.py --login op1 --name "Оператор 1" --role operator
    python scripts/create_operator.py --login owner --reset-password
    python scripts/create_operator.py --list

The password is prompted interactively (not passed via argv — keeps it out of shell history).
Reads MONGO_URL / MONGO_DB from .env — RUN FROM THE PROJECT ROOT (cd /opt/operator-panel),
otherwise the .env file is not found and the script talks to localhost defaults.
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


def ask_password() -> str:
    password = getpass.getpass("Пароль (мин. 8 символов): ")
    if len(password) < 8:
        sys.exit("Пароль слишком короткий")
    if password != getpass.getpass("Повторите пароль: "):
        sys.exit("Пароли не совпадают")
    return password


async def main() -> None:
    parser = argparse.ArgumentParser(description="Manage operator accounts")
    parser.add_argument("--login")
    parser.add_argument("--name")
    parser.add_argument("--role", choices=["operator", "owner"], default="operator")
    parser.add_argument("--reset-password", action="store_true",
                        help="Сбросить пароль существующей учётки")
    parser.add_argument("--list", action="store_true", help="Показать все учётки")
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_url, serverSelectionTimeoutMS=5000)
    col = client[settings.mongo_db][settings.operators_collection]

    if args.list:
        print(f"База: {settings.mongo_db}, коллекция: {settings.operators_collection}")
        count = 0
        async for op in col.find({}, {"login": 1, "name": 1, "role": 1, "active": 1}):
            count += 1
            print(f"  {op['login']:20} {op.get('role', '?'):9} "
                  f"{'активен' if op.get('active') else 'ОТКЛЮЧЁН':9} {op.get('name', '')}")
        if not count:
            print("  (пусто — учёток нет)")
        client.close()
        return

    if not args.login:
        sys.exit("Укажите --login (или --list)")
    login = args.login.strip().lower()
    existing = await col.find_one({"login": login})

    if args.reset_password:
        if not existing:
            sys.exit(f"Учётка «{login}» не найдена. Список: --list")
        await col.update_one({"_id": existing["_id"]},
                             {"$set": {"password_hash": hash_password(ask_password())}})
        print(f"Пароль для «{login}» обновлён")
        client.close()
        return

    if not args.name:
        sys.exit("Укажите --name")
    if existing:
        sys.exit(f"Логин «{login}» уже существует (сброс пароля: --reset-password)")

    await col.insert_one({
        "login": login,
        "password_hash": hash_password(ask_password()),
        "name": args.name,
        "role": args.role,
        "active": True,
        "created_at": utcnow(),
        "created_by": "console",
        "last_login_at": None,
    })
    print(f"Создан {args.role}: {login}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
