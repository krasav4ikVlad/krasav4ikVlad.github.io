"""Survival math for /users/lifetime against an in-memory Mongo."""

from datetime import datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.routers.users as users_mod

NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_lifetime_survival(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True)["t"]
    docs = []
    # 4 ушли, прожив 60 дней; 2 ушли, прожив 200; 4 живут уже 300 дней
    for i in range(4):
        docs.append({"_id": i, "first_topup_at": NOW - timedelta(days=100),
                     "sub_until": NOW - timedelta(days=40),
                     "segment": "expired_7d", "topup_total": 500})
    for i in range(4, 6):
        docs.append({"_id": i, "first_topup_at": NOW - timedelta(days=260),
                     "sub_until": NOW - timedelta(days=60),
                     "segment": "churned_60d", "topup_total": 1500})
    for i in range(6, 10):
        docs.append({"_id": i, "first_topup_at": NOW - timedelta(days=300),
                     "segment": "active_paid", "topup_total": 3000})
    await db["users_flat"].insert_many(docs)
    monkeypatch.setattr(users_mod, "get_db", lambda: db)

    out = await users_mod._lifetime()

    assert out["paying_total"] == 10
    assert out["departed"] == 6 and out["alive"] == 4
    assert out["median_lifetime_days"] == 60.0
    assert out["avg_ltv_departed"] == pytest.approx(833.33, abs=0.01)

    s = {p["month"]: p["pct"] for p in out["survival"]}
    assert s[1] == 100.0          # до 30 дней дожили все
    assert s[3] == 60.0           # к 90-му дню живы 2 ушедших-позже + 4 живых
    assert s[7] == 40.0           # к 210-му дню — только 4 живых
    # живые моложе горизонта не тянут кривую вниз: месяц 9 (270д) — 4 из 10
    assert out["median_survival_months"] == 7

    hist = {h["bucket"]: h["count"] for h in out["histogram"]}
    assert hist["2–3 мес"] == 4   # ровно 60 дней = граница второго месяца
    assert hist["6–12 мес"] == 2  # 200 дней
