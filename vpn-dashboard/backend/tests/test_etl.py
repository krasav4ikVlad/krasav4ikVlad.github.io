"""ETL tests against an in-memory Mongo (mongomock-motor)."""

from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.db import TX_FLAT, USERS_FLAT
from app.etl import extract_user_id, flatten_user, parse_segment_history, run_etl

UTC = timezone.utc
NOW = datetime(2024, 6, 1, tzinfo=UTC)


def raw_user_mixed_formats():
    return {
        "_id": 111222333,
        "username": "TestUser",
        "joined_at": datetime(2024, 1, 15, tzinfo=UTC),
        "balance": 50,
        "growth": {"segment": "active_paid", "days_to_expire": 12},
        "growth_history": [
            {"segment": "new_trial_d2", "dt": datetime(2024, 1, 15, tzinfo=UTC)},
            {"segment": "active_paid", "dt": datetime(2024, 1, 20, tzinfo=UTC)},
        ],
        "ref_stats": {"turnover_total": 1500, "referrals": 5,
                      "paying_referrals": 2, "payout_history": []},
        "extraDevices": [{"active": True}, {"active": False}],
        "preferred_client": "happ",
        "info": {
            "transactions": [
                # format 1: legacy array
                [150, {"$date": "2024-01-20T10:00:00Z"},
                 "Пополнение (cardlink)", "p1"],
                # format 2: modern object
                {"amount": 300, "dt": datetime(2024, 2, 20, tzinfo=UTC),
                 "type": "topup", "meta": {"source": "severpay", "bonus_rub": 60}},
                # format 3: wrapped object
                [{"amount": 45, "dt": datetime(2024, 3, 1, tzinfo=UTC),
                  "type": "ref_income", "meta": {"from_user": 7}}],
                # promo
                [100, datetime(2024, 3, 5, tzinfo=UTC), "Промокод WELCOME"],
            ],
        },
        "logs_balance": [
            [199, datetime(2024, 1, 21, tzinfo=UTC), "Продление подписки Pro"],
            [199, datetime(2024, 2, 21, tzinfo=UTC), "Продление подписки Pro"],
            {"amount": 50, "dt": datetime(2024, 2, 25, tzinfo=UTC), "type": "bypass"},
            [99, datetime(2024, 3, 2, tzinfo=UTC), "Доп. устройство"],
        ],
    }


class TestFlattenUser:
    def test_produces_rows_for_all_formats(self):
        rows, user_row, unparsed = flatten_user(raw_user_mixed_formats(), NOW)
        assert unparsed == 0
        credits = [r for r in rows if r["direction"] == "credit"]
        debits = [r for r in rows if r["direction"] == "debit"]
        assert len(credits) == 4
        assert len(debits) == 4
        assert {c["kind"] for c in credits} == {"topup", "ref_income", "promo"}
        assert {d["kind"] for d in debits} == {"renewal", "bypass", "device"}

    def test_user_row_projection(self):
        _, u, _ = flatten_user(raw_user_mixed_formats(), NOW)
        assert u["_id"] == 111222333
        assert u["segment"] == "active_paid"
        assert u["username_lower"] == "testuser"
        assert u["first_topup_at"] == datetime(2024, 1, 20, 10, 0, tzinfo=UTC)
        assert u["topup_total"] == 450.0
        assert u["bonus_total"] == 60.0
        assert u["first_sub_at"] == datetime(2024, 1, 21, tzinfo=UTC)
        assert u["second_sub_at"] == datetime(2024, 2, 21, tzinfo=UTC)
        assert u["renewals_count"] == 2
        assert u["bypass_count"] == 1 and u["bypass_total"] == 50.0
        assert u["extra_devices_active"] == 1
        assert u["promo_activations"][0]["code"] == "WELCOME"
        assert len(u["segment_history"]) == 2

    def test_deterministic_ids(self):
        rows1, _, _ = flatten_user(raw_user_mixed_formats(), NOW)
        rows2, _, _ = flatten_user(raw_user_mixed_formats(), NOW)
        assert [r["_id"] for r in rows1] == [r["_id"] for r in rows2]

    def test_user_without_id_skipped(self):
        rows, user_row, _ = flatten_user({"username": "x"}, NOW)
        assert rows == [] and user_row is None


class TestExtractUserId:
    def test_from_int_id(self):
        assert extract_user_id({"_id": 42}) == 42

    def test_tg_id_preferred(self):
        assert extract_user_id({"_id": "oid", "tg_id": 42}) == 42

    def test_numeric_string(self):
        assert extract_user_id({"_id": "12345"}) == 12345

    def test_no_id(self):
        assert extract_user_id({"_id": "not-a-number"}) is None

    def test_objectid_with_user_id_field(self):
        # the common real-world shape: _id is an ObjectId, tg id in user_id
        assert extract_user_id({"_id": object(), "user_id": 802421217}) == 802421217

    @pytest.mark.parametrize("field", ["tgid", "chat_id", "id", "uid", "tg"])
    def test_alternative_field_names(self, field):
        assert extract_user_id({"_id": object(), field: 77}) == 77

    def test_nested_in_info(self):
        assert extract_user_id({"_id": object(),
                                "info": {"tg_id": 99}}) == 99

    def test_float_integer(self):
        assert extract_user_id({"user_id": 42.0}) == 42

    def test_bool_rejected(self):
        assert extract_user_id({"user_id": True, "_id": "x"}) is None


class TestParseSegmentHistory:
    def test_dict_entries(self):
        h = parse_segment_history([
            {"segment": "expired", "dt": "2024-02-01T00:00:00Z"},
            {"segment": "active_paid", "dt": "2024-01-01T00:00:00Z"},
        ])
        assert [x["segment"] for x in h] == ["active_paid", "expired"]  # sorted

    def test_pair_entries(self):
        h = parse_segment_history([["active_paid", "2024-01-01T00:00:00Z"]])
        assert h[0]["segment"] == "active_paid"

    def test_reversed_pair(self):
        h = parse_segment_history([["2024-01-01T00:00:00Z", "active_paid"]])
        assert h[0]["segment"] == "active_paid"

    def test_garbage(self):
        assert parse_segment_history(None) == []
        assert parse_segment_history("x") == []
        assert parse_segment_history([{"no": "fields"}]) == []


@pytest.mark.asyncio
class TestRunEtl:
    async def _db(self):
        client = AsyncMongoMockClient(tz_aware=True)
        return client["testdb"]

    async def test_full_sweep(self):
        db = await self._db()
        await db["users"].insert_one(raw_user_mixed_formats())
        await db["users"].insert_one({
            "_id": 999, "username": "second",
            "info": {"transactions": [[500, NOW, "Пополнение (wata)"]]},
        })
        stats = await run_etl(db)
        assert stats["users"] == 2
        assert await db[TX_FLAT].count_documents({}) == 9
        assert await db[USERS_FLAT].count_documents({}) == 2
        state = await db["etl_state"].find_one({"_id": "etl"})
        assert state["tx_rows"] == 9

    async def test_idempotent_rerun(self):
        db = await self._db()
        await db["users"].insert_one(raw_user_mixed_formats())
        await run_etl(db)
        first = await db[TX_FLAT].count_documents({})
        await run_etl(db)
        assert await db[TX_FLAT].count_documents({}) == first

    async def test_stale_rows_removed_when_source_shrinks(self):
        db = await self._db()
        doc = raw_user_mixed_formats()
        await db["users"].insert_one(doc)
        await run_etl(db)
        before = await db[TX_FLAT].count_documents({"user_id": doc["_id"]})
        await db["users"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"info.transactions": doc["info"]["transactions"][:1]}})
        await run_etl(db)
        after = await db[TX_FLAT].count_documents({"user_id": doc["_id"]})
        assert after == before - 3

    async def test_broken_document_does_not_kill_run(self):
        db = await self._db()
        await db["users"].insert_one({"_id": 1, "info": {"transactions": "trash"}})
        await db["users"].insert_one(raw_user_mixed_formats())
        stats = await run_etl(db)
        assert stats["users"] == 2
        assert stats["unparsed"] == 1
