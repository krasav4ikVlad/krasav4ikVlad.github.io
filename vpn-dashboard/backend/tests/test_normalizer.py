"""Unit tests for the transaction/debit normalization layer.

Covers all three historical formats of ``info.transactions``, every date
representation seen in the wild, Russian description parsing, and garbage
resilience.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.normalizer import (
    DebitKind,
    NormalizedTransaction,
    TxKind,
    normalize_debit_entry,
    normalize_debits,
    normalize_source,
    normalize_transaction_entry,
    normalize_transactions,
    parse_amount,
    parse_description,
    parse_dt,
)

UTC = timezone.utc
DT = datetime(2024, 5, 1, 10, 0, 0, tzinfo=UTC)
DT_MS = int(DT.timestamp() * 1000)


def one(entry):
    """Normalize an entry and assert exactly one transaction came out."""
    txs = normalize_transaction_entry(entry)
    assert len(txs) == 1, f"expected 1 tx, got {txs!r}"
    return txs[0]


# ---------------------------------------------------------------------------
# parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    def test_datetime_aware_passthrough(self):
        assert parse_dt(DT) == DT

    def test_datetime_naive_becomes_utc(self):
        naive = datetime(2024, 5, 1, 10, 0, 0)
        assert parse_dt(naive) == DT

    def test_extended_json_iso_string(self):
        assert parse_dt({"$date": "2024-05-01T10:00:00Z"}) == DT

    def test_extended_json_number_long(self):
        assert parse_dt({"$date": {"$numberLong": str(DT_MS)}}) == DT

    def test_epoch_millis_int(self):
        assert parse_dt(DT_MS) == DT

    def test_epoch_seconds_int(self):
        assert parse_dt(int(DT.timestamp())) == DT

    def test_epoch_seconds_float(self):
        assert parse_dt(DT.timestamp()) == DT

    def test_iso_string_with_offset(self):
        assert parse_dt("2024-05-01T13:00:00+03:00") == DT

    def test_iso_string_naive_assumed_utc(self):
        assert parse_dt("2024-05-01T10:00:00") == DT

    def test_russian_date_format_is_moscow_time(self):
        # the bot writes "%d.%m.%Y ..." strings in MSK (UTC+3)
        assert parse_dt("01.05.2024 13:00:00") == DT

    def test_numeric_string_millis(self):
        assert parse_dt(str(DT_MS)) == DT

    @pytest.mark.parametrize("garbage", [None, "", "not a date", {}, [], True, -5, float("nan")])
    def test_garbage_returns_none(self, garbage):
        assert parse_dt(garbage) is None

    def test_small_ints_are_not_1970_dates(self):
        # amounts/ids must never be mistaken for epoch timestamps
        assert parse_dt(150) is None
        assert parse_dt(99.5) is None

    def test_aware_dt_normalized_to_utc(self):
        msk = datetime(2024, 5, 1, 13, 0, tzinfo=timezone(timedelta(hours=3)))
        result = parse_dt(msk)
        assert result == DT
        assert result.tzinfo == UTC
        assert result.hour == 10  # heatmap buckets must be UTC hours


# ---------------------------------------------------------------------------
# parse_amount
# ---------------------------------------------------------------------------


class TestParseAmount:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (150, 150.0),
            (99.5, 99.5),
            (-30, -30.0),
            ("150", 150.0),
            ("99,50", 99.5),
            ("1 200", 1200.0),
            ("150₽", 150.0),
            ({"$numberInt": "150"}, 150.0),
            ({"$numberDouble": "99.5"}, 99.5),
            ({"$numberLong": "1000"}, 1000.0),
        ],
    )
    def test_valid(self, value, expected):
        assert parse_amount(value) == expected

    @pytest.mark.parametrize("garbage", [None, "", "abc", {}, [], True, float("inf")])
    def test_garbage_returns_none(self, garbage):
        assert parse_amount(garbage) is None


# ---------------------------------------------------------------------------
# Format 1: legacy array
# ---------------------------------------------------------------------------


class TestLegacyFormat:
    def test_basic_cardlink_topup(self):
        tx = one([150, {"$date": "2024-05-01T10:00:00Z"}, "Пополнение (cardlink)", "pay_abc"])
        assert tx.amount == 150.0
        assert tx.dt == DT
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "cardlink"
        assert tx.payment_id == "pay_abc"
        assert tx.bonus == 0.0

    def test_without_payment_id(self):
        tx = one([300, DT, "Пополнение (severpay)"])
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "severpay"
        assert tx.payment_id is None

    def test_card_topup_without_parens(self):
        tx = one([500, DT, "Пополнение картой"])
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "card"

    def test_referral_description(self):
        tx = one([45, DT, "Реферал: начисление 10%"])
        assert tx.kind is TxKind.REF_INCOME
        assert tx.source is None

    def test_purchase_pro(self):
        tx = one([-199, DT, "Покупка Pro"])
        assert tx.kind is TxKind.PURCHASE
        assert tx.amount == -199.0

    def test_promo_code(self):
        tx = one([100, DT, "Промокод WELCOME100"])
        assert tx.kind is TxKind.PROMO
        assert tx.promo_code == "WELCOME100"

    def test_promo_code_in_quotes(self):
        tx = one([50, DT, "Активирован промокод «НОВЫЙГОД»"])
        assert tx.kind is TxKind.PROMO
        assert tx.promo_code == "НОВЫЙГОД"

    def test_bonus_suffix_on_topup(self):
        tx = one([240, DT, "Пополнение (cardlink) + бонус 40₽"])
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "cardlink"
        assert tx.bonus == 40.0

    def test_promo_action_suffix(self):
        tx = one([120, DT, "Пополнение (wata) + акция 20₽"])
        assert tx.source == "wata"
        assert tx.bonus == 20.0

    def test_standalone_bonus_accrual(self):
        tx = one([40, DT, "+ бонус 40₽"])
        assert tx.kind is TxKind.BONUS
        assert tx.bonus == 40.0

    def test_returning_bonus_is_not_topup_revenue(self):
        # production wording: gifted retention bonus, not provider revenue
        tx = one([40, "2026-06-04T13:47:51.064Z", "Бонус за возвращение (серия)"])
        assert tx.kind is TxKind.BONUS
        assert tx.bonus == 40.0
        assert tx.source is None

    def test_promo_action_bonus_word(self):
        tx = one([100, DT, "Акция +20%"])
        assert tx.kind is TxKind.BONUS

    def test_comma_bonus_wording(self):
        # current bot: "Пополнение (tribute), бонус 30₽" — no plus sign;
        # клиент реально заплатил amount − 30
        tx = one([130, DT, "Пополнение (tribute), бонус 30₽"])
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "tribute"
        assert tx.bonus == 30.0

    def test_refund_is_not_revenue(self):
        tx = one({"amount": 100, "dt": DT,
                  "description": "Возврат: Пополнение (cardlink)"})
        assert tx.kind is TxKind.REFUND

    def test_schema_format_c_promo(self):
        """Format C from the schema doc: type/code/created_at/comment."""
        tx = one({"type": "promo_balance", "amount": 12, "code": "WELCOME",
                  "created_at": DT, "comment": "Активация промокода WELCOME"})
        assert tx.kind is TxKind.PROMO
        assert tx.promo_code == "WELCOME"
        assert tx.dt == DT
        assert tx.desc == "Активация промокода WELCOME"

    def test_negative_renewal_dict(self):
        """Current bot writes spends as negative rows in transactions."""
        tx = one({"amount": -3, "dt": DT,
                  "description": "Продление подписки «Ежедневная»"})
        assert tx.amount == -3.0
        assert tx.kind is not TxKind.TOPUP

    def test_no_description_positive_defaults_to_topup(self):
        tx = one([150, DT])
        assert tx.kind is TxKind.TOPUP
        assert tx.source is None

    def test_no_description_negative_is_unknown(self):
        tx = one([-150, DT])
        assert tx.kind is TxKind.UNKNOWN

    def test_amount_as_numeric_string(self):
        tx = one(["150", DT, "Пополнение (heleket)"])
        assert tx.amount == 150.0
        assert tx.source == "heleket"

    def test_raw_preserved(self):
        raw = [150, DT, "Пополнение (cardlink)"]
        tx = one(raw)
        assert tx.raw == raw


# ---------------------------------------------------------------------------
# Format 2: modern object
# ---------------------------------------------------------------------------


class TestModernObjectFormat:
    def test_topup_with_meta(self):
        tx = one({
            "amount": 150,
            "dt": {"$date": "2024-05-01T10:00:00Z"},
            "type": "topup",
            "meta": {"source": "cardlink", "bonus_rub": 0},
        })
        assert tx.kind is TxKind.TOPUP
        assert tx.amount == 150.0
        assert tx.dt == DT
        assert tx.source == "cardlink"
        assert tx.bonus == 0.0

    def test_topup_with_bonus(self):
        tx = one({
            "amount": 240, "dt": DT, "type": "topup",
            "meta": {"source": "severpay", "bonus_rub": 40},
        })
        assert tx.bonus == 40.0

    def test_ref_income_keeps_meta(self):
        meta = {"from_user": 123456, "percent": 10}
        tx = one({"amount": 45, "dt": DT, "type": "ref_income", "meta": meta})
        assert tx.kind is TxKind.REF_INCOME
        assert tx.ref_meta == meta

    def test_promo_balance(self):
        tx = one({
            "amount": 100, "dt": DT, "type": "promo_balance",
            "meta": {"promo_code": "WELCOME100"},
        })
        assert tx.kind is TxKind.PROMO
        assert tx.promo_code == "WELCOME100"

    def test_type_takes_precedence_over_description(self):
        tx = one({
            "amount": 45, "dt": DT, "type": "ref_income",
            "desc": "Пополнение (cardlink)",  # lying description
        })
        assert tx.kind is TxKind.REF_INCOME

    def test_description_fills_missing_type(self):
        tx = one({"amount": 150, "dt": DT, "desc": "Пополнение (wata)"})
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "wata"

    def test_meta_source_without_type_implies_topup(self):
        tx = one({"amount": 150, "dt": DT, "meta": {"source": "cards_ru"}})
        assert tx.kind is TxKind.TOPUP
        assert tx.source == "cards_ru"

    def test_unknown_type_string_falls_back_to_desc(self):
        tx = one({"amount": 100, "dt": DT, "type": "weird_new_type",
                  "desc": "Промокод SUMMER"})
        assert tx.kind is TxKind.PROMO

    def test_payment_id_from_meta(self):
        tx = one({"amount": 100, "dt": DT, "type": "topup",
                  "meta": {"source": "cardlink", "payment_id": "inv_1"}})
        assert tx.payment_id == "inv_1"

    def test_epoch_millis_dt(self):
        tx = one({"amount": 100, "dt": DT_MS, "type": "topup"})
        assert tx.dt == DT


# ---------------------------------------------------------------------------
# Format 3: object wrapped in array
# ---------------------------------------------------------------------------


class TestWrappedFormat:
    def test_single_wrapped_object(self):
        tx = one([{
            "amount": 150, "dt": DT, "type": "topup",
            "meta": {"source": "cardlink", "bonus_rub": 0},
        }])
        assert tx.kind is TxKind.TOPUP
        assert tx.amount == 150.0
        assert tx.source == "cardlink"

    def test_multiple_wrapped_objects(self):
        txs = normalize_transaction_entry([
            {"amount": 150, "dt": DT, "type": "topup", "meta": {"source": "wata"}},
            {"amount": 45, "dt": DT, "type": "ref_income"},
        ])
        assert len(txs) == 2
        assert txs[0].kind is TxKind.TOPUP
        assert txs[1].kind is TxKind.REF_INCOME

    def test_nested_wrapping(self):
        tx = one([[{"amount": 10, "dt": DT, "type": "topup"}]])
        assert tx.amount == 10.0

    def test_not_confused_with_legacy(self):
        # legacy starts with a number; wrapped starts with a dict
        legacy = normalize_transaction_entry([150, DT, "Пополнение (cardlink)"])
        wrapped = normalize_transaction_entry([{"amount": 150, "dt": DT, "type": "topup"}])
        assert legacy[0].source == "cardlink"
        assert wrapped[0].kind is TxKind.TOPUP


# ---------------------------------------------------------------------------
# Whole-field normalization with mixed formats
# ---------------------------------------------------------------------------


class TestNormalizeTransactionsField:
    def test_mixed_formats_in_one_document(self):
        raw = [
            [150, {"$date": "2024-05-01T10:00:00Z"}, "Пополнение (cardlink)", "p1"],
            {"amount": 300, "dt": DT, "type": "topup",
             "meta": {"source": "severpay", "bonus_rub": 60}},
            [{"amount": 45, "dt": DT, "type": "ref_income", "meta": {"from_user": 7}}],
            [100, DT, "Промокод WELCOME100"],
        ]
        txs, unparsed = normalize_transactions(raw)
        assert unparsed == 0
        assert [t.kind for t in txs] == [
            TxKind.TOPUP, TxKind.TOPUP, TxKind.REF_INCOME, TxKind.PROMO,
        ]
        assert sum(t.amount for t in txs) == 595.0

    def test_garbage_entries_counted_not_raised(self):
        raw = [
            "какая-то строка",
            {"foo": "bar"},
            [150, DT, "Пополнение (cardlink)"],
            None,
        ]
        txs, unparsed = normalize_transactions(raw)
        assert len(txs) == 1
        assert unparsed == 2  # None is skipped silently, str and bad dict counted

    def test_non_list_field(self):
        txs, unparsed = normalize_transactions("oops")
        assert txs == [] and unparsed == 1

    def test_none_field(self):
        assert normalize_transactions(None) == ([], 0)

    def test_dict_keyed_field(self):
        raw = {"0": [150, DT, "Пополнение (cardlink)"],
               "1": {"amount": 45, "dt": DT, "type": "ref_income"}}
        txs, unparsed = normalize_transactions(raw)
        assert len(txs) == 2 and unparsed == 0

    def test_whole_field_is_single_flat_legacy_row(self):
        # info.transactions = [150, dt, "desc"] without the outer list —
        # must be one transaction, not shredded into 3 unparsed scalars
        txs, unparsed = normalize_transactions(
            [150, DT, "Пополнение (cardlink)"])
        assert unparsed == 0
        assert len(txs) == 1
        assert txs[0].amount == 150.0 and txs[0].source == "cardlink"

    def test_extended_json_amount_head_is_legacy(self):
        txs, unparsed = normalize_transactions(
            [[{"$numberInt": "150"}, {"$date": "2024-05-01T10:00:00Z"},
              "Пополнение (wata)"]])
        assert unparsed == 0
        assert txs[0].amount == 150.0 and txs[0].source == "wata"

    def test_explicit_zero_bonus_not_overridden_by_desc(self):
        tx = one({"amount": 240, "dt": DT, "type": "topup",
                  "desc": "Пополнение (cardlink) + бонус 40₽",
                  "meta": {"source": "cardlink", "bonus_rub": 0}})
        assert tx.bonus == 0.0  # explicit meta zero wins over the description


# ---------------------------------------------------------------------------
# Description parser details
# ---------------------------------------------------------------------------


class TestParseDescription:
    @pytest.mark.parametrize(
        "desc,source",
        [
            ("Пополнение (cardlink)", "cardlink"),
            ("Пополнение (severpay)", "severpay"),
            ("Пополнение (wata)", "wata"),
            ("Пополнение (heleket)", "heleket"),
            ("Пополнение (cards_ru)", "cards_ru"),
            ("Пополнение баланса (CardLink)", "cardlink"),
            ("Пополнение картой", "card"),
        ],
    )
    def test_sources(self, desc, source):
        assert parse_description(desc).source == source

    def test_unknown_source_kept_lowercase(self):
        assert parse_description("Пополнение (NewPay)").source == "newpay"

    @pytest.mark.parametrize(
        "desc,bonus",
        [
            ("Пополнение (cardlink) + бонус 40₽", 40.0),
            ("Пополнение (wata) + акция 100 ₽", 100.0),
            ("+ бонус 25.5₽", 25.5),
            ("Пополнение (cardlink)", 0.0),
        ],
    )
    def test_bonus_extraction(self, desc, bonus):
        assert parse_description(desc).bonus == bonus

    def test_empty_and_none(self):
        assert parse_description(None).kind is None
        assert parse_description("").kind is None
        assert parse_description("   ").kind is None


class TestNormalizeSource:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("cardlink", "cardlink"),
            ("CardLink", "cardlink"),
            ("card_link", "cardlink"),
            ("cards", "cards_ru"),
            ("telegram_stars", "stars"),
            ("CryptoBot", "crypto"),
            ("  wata  ", "wata"),
            (None, None),
            ("", None),
        ],
    )
    def test_aliases(self, value, expected):
        assert normalize_source(value) == expected


# ---------------------------------------------------------------------------
# logs_balance debits
# ---------------------------------------------------------------------------


class TestDebits:
    def test_legacy_renewal(self):
        [d] = normalize_debit_entry([199, DT, "Продление подписки Pro на 30 дней"])
        assert d.kind is DebitKind.RENEWAL
        assert d.amount == 199.0
        assert d.dt == DT

    def test_negative_amount_stored_absolute(self):
        [d] = normalize_debit_entry([-199, DT, "Продление подписки"])
        assert d.amount == 199.0

    def test_device_purchase(self):
        [d] = normalize_debit_entry([99, DT, "Доп. устройство (слот 2)"])
        assert d.kind is DebitKind.DEVICE

    def test_bypass_purchase(self):
        [d] = normalize_debit_entry([50, DT, "ByPass трафик 5 ГБ"])
        assert d.kind is DebitKind.BYPASS

    def test_gift(self):
        [d] = normalize_debit_entry([199, DT, "Подарок для @friend"])
        assert d.kind is DebitKind.GIFT

    def test_dict_format_with_type(self):
        [d] = normalize_debit_entry({"amount": 99, "dt": DT, "type": "device",
                                     "desc": "Слот 3"})
        assert d.kind is DebitKind.DEVICE

    def test_dict_type_beats_desc(self):
        [d] = normalize_debit_entry({"amount": 50, "dt": DT, "type": "bypass",
                                     "desc": "Продление"})
        assert d.kind is DebitKind.BYPASS

    def test_purchase_pro_maps_to_renewal(self):
        [d] = normalize_debit_entry([199, DT, "Покупка Pro"])
        assert d.kind is DebitKind.RENEWAL
        assert d.product == "Pro"

    def test_unknown_desc_is_other(self):
        [d] = normalize_debit_entry([10, DT, "Загадочная операция"])
        assert d.kind is DebitKind.OTHER

    def test_whole_field_mixed(self):
        raw = [
            [199, DT, "Продление подписки"],
            {"amount": 99, "dt": DT, "type": "device"},
            [{"amount": 50, "dt": DT, "type": "bypass"}],
            "мусор",
        ]
        debits, unparsed = normalize_debits(raw)
        assert [d.kind for d in debits] == [
            DebitKind.RENEWAL, DebitKind.DEVICE, DebitKind.BYPASS,
        ]
        assert unparsed == 1

    def test_rs2_details_timestamp_format(self):
        """Production format: details + timestamp keys, Russian dd.mm.yyyy
        strings are Moscow time (UTC+3)."""
        [d] = normalize_debit_entry({"amount": 50,
                                     "details": "Покупка 5 гигабайт",
                                     "timestamp": "05.07.2026 16:38:23"})
        assert d.kind is DebitKind.BYPASS
        assert d.amount == 50.0
        assert d.dt == datetime(2026, 7, 5, 13, 38, 23, tzinfo=UTC)

    def test_rs2_gift_subscription(self):
        [d] = normalize_debit_entry({"amount": 2000,
                                     "details": "Дарение подписки",
                                     "timestamp": "06.07.2026 12:37:41"})
        assert d.kind is DebitKind.GIFT
        assert d.dt is not None

    def test_none_field(self):
        assert normalize_debits(None) == ([], 0)


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


class TestModelContract:
    def test_required_fields_present(self):
        tx = one([150, DT, "Пополнение (cardlink)"])
        data = tx.model_dump()
        for field in ("amount", "dt", "kind", "source", "bonus",
                      "promo_code", "ref_meta", "raw"):
            assert field in data

    def test_serializable(self):
        tx = one({"amount": 150, "dt": DT, "type": "topup",
                  "meta": {"source": "cardlink"}})
        assert isinstance(tx.model_dump_json(), str)

    def test_never_raises_on_fuzz(self):
        fuzz = [
            0, -1, 3.14, "", "x", [], {}, [[]], [{}], [[[]]], [None, None],
            {"amount": float("nan")}, [float("inf"), None], [True, False],
            {"dt": {"$date": {}}}, [1e308, 1e308], ["", "", "", "", ""],
        ]
        for entry in fuzz:
            normalize_transaction_entry(entry)  # must not raise
            normalize_debit_entry(entry)        # must not raise
