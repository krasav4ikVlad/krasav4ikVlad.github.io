"""Tests for payments_webhook normalization and the payments_flat sweep."""

from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.db import PAYMENTS_FLAT
from app.etl import sweep_payments
from app.normalizer import normalize_payment_webhook

UTC = timezone.utc
NOW = datetime(2026, 7, 15, tzinfo=UTC)


def webhook_doc(**overrides):
    doc = {
        "_id": "6917c091689fd338958ae771",
        "txid": "3a1d96f4-0000-5d64-67e0-cac48670d740",
        "amount_rub": 100,
        "created_at": datetime(2025, 11, 15, 2, 51, 45, tzinfo=UTC),
        "payload": {
            "transactionType": "SBP",
            "transactionId": "3a1d96f4-0000-5d64-67e0-cac48670d740",
            "transactionStatus": "Paid",
            "errorCode": None,
            "terminalName": "bot сбп",
            "amount": 100,
            "currency": "RUB",
            "orderId": "d9145ac7-0000-4342-8dce-6ebe644411cf",
            "orderDescription": "Пополнение баланса 123456789 на 100₽",
            "paymentTime": "2025-11-14T23:51:32.765513Z",
            "commission": 10,
            "email": "user@example.com",
        },
        "processed": True,
        "source": "wata",
        "user_id": 123456789,
    }
    doc.update(overrides)
    return doc


class TestNormalizePaymentWebhook:
    def test_full_document(self):
        p = normalize_payment_webhook(webhook_doc())
        assert p is not None
        assert p.txid == "3a1d96f4-0000-5d64-67e0-cac48670d740"
        assert p.amount == 100.0
        assert p.commission == 10.0
        assert p.source == "wata"
        assert p.status == "paid"
        assert p.user_id == 123456789
        assert p.tx_type == "SBP"
        assert p.dt == datetime(2025, 11, 15, 2, 51, 45, tzinfo=UTC)

    def test_pii_not_retained(self):
        p = normalize_payment_webhook(webhook_doc())
        dumped = p.model_dump_json()
        assert "user@example.com" not in dumped
        assert "orderDescription" not in dumped
        assert "Пополнение баланса" not in dumped

    @pytest.mark.parametrize("status,expected", [
        ("Paid", "paid"), ("SUCCESS", "paid"), ("Completed", "paid"),
        ("Failed", "failed"), ("Declined", "failed"), ("Canceled", "failed"),
        ("Pending", "pending"), ("Created", "pending"),
        ("weird", "other"),
    ])
    def test_status_mapping(self, status, expected):
        doc = webhook_doc()
        doc["payload"]["transactionStatus"] = status
        assert normalize_payment_webhook(doc).status == expected

    def test_processed_true_without_status_means_paid(self):
        doc = webhook_doc()
        del doc["payload"]["transactionStatus"]
        assert normalize_payment_webhook(doc).status == "paid"

    def test_missing_payload(self):
        p = normalize_payment_webhook({
            "txid": "t1", "amount_rub": 50,
            "created_at": NOW, "source": "cardlink", "user_id": 5,
        })
        assert p.amount == 50.0 and p.source == "cardlink"
        assert p.status == "other"

    def test_txid_fallback_to_oid(self):
        doc = webhook_doc()
        del doc["txid"]
        del doc["payload"]["transactionId"]
        assert normalize_payment_webhook(doc).txid == "6917c091689fd338958ae771"

    def test_garbage(self):
        assert normalize_payment_webhook(None) is None
        assert normalize_payment_webhook({}) is None
        assert normalize_payment_webhook("x") is None


@pytest.mark.asyncio
class TestSweepPayments:
    async def test_sweep_and_idempotency(self):
        db = AsyncMongoMockClient(tz_aware=True)["testdb"]
        await db["payments_webhook"].insert_many([
            webhook_doc(),
            webhook_doc(_id="oid2", txid="tx2", user_id=2,
                        payload={"transactionStatus": "Failed", "commission": 0}),
        ])
        n = await sweep_payments(db, NOW, full=True)
        assert n == 2
        assert await db[PAYMENTS_FLAT].count_documents({}) == 2
        # re-run: same rows replaced, no duplicates
        await sweep_payments(db, NOW, full=True)
        assert await db[PAYMENTS_FLAT].count_documents({}) == 2
        failed = await db[PAYMENTS_FLAT].find_one({"_id": "tx2"})
        assert failed["status"] == "failed"

    async def test_absent_collection_is_noop(self):
        db = AsyncMongoMockClient(tz_aware=True)["testdb"]
        assert await sweep_payments(db, NOW) == 0
