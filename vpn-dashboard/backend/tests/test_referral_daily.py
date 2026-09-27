"""Per-referrer daily referral stats (/users/{id}/referral-daily)."""

from datetime import datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.routers.users as users_mod

NOW = datetime.now(timezone.utc)
D = lambda days: NOW - timedelta(days=days)  # noqa: E731


@pytest.mark.asyncio
async def test_referral_daily(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True)["t"]
    # два реферала: один пришёл 5 дней назад, второй 2 дня назад (строковый
    # referrer_id — легаси), третий юзер чужой
    await db["users_flat"].insert_many([
        {"_id": 201, "referrer_id": 100, "joined_at": D(5)},
        {"_id": 202, "referrer_id": "100", "joined_at": D(2)},
        {"_id": 203, "referrer_id": 999, "joined_at": D(2)},
    ])
    await db["transactions_flat"].insert_many([
        # пополнения рефералов (у второго — с бонусом 20)
        {"user_id": 201, "direction": "credit", "kind": "topup",
         "amount": 100.0, "bonus": 0.0, "dt": D(4)},
        {"user_id": 202, "direction": "credit", "kind": "topup",
         "amount": 170.0, "bonus": 20.0, "dt": D(1)},
        # пополнение чужого юзера — не должно попасть
        {"user_id": 203, "direction": "credit", "kind": "topup",
         "amount": 999.0, "bonus": 0.0, "dt": D(1)},
        # начисление рефереру
        {"user_id": 100, "direction": "credit", "kind": "ref_income",
         "amount": 10.0, "dt": D(4)},
    ])
    monkeypatch.setattr(users_mod, "get_db", lambda: db)

    out = await users_mod._referral_daily(user_id=100, from_iso=None, to_iso=None)

    assert out["referrals_total"] == 2
    assert out["sum_registrations"] == 2
    assert out["sum_topups"] == 250.0        # 100 + (170 − 20 бонус)
    assert out["sum_ref_income"] == 10.0

    by_day = {s["day"]: s for s in out["series"]}
    d4 = by_day[D(4).date().isoformat()]
    assert d4["topups"] == 100.0 and d4["ref_income"] == 10.0
    d1 = by_day[D(1).date().isoformat()]
    assert d1["topups"] == 150.0 and d1["topup_count"] == 1
