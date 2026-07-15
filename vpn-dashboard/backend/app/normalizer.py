"""Normalization layer for heterogeneous transaction/debit history formats.

The ``users`` collection accumulated three historical formats of
``info.transactions`` entries (often mixed inside a single document):

1. Legacy array::

       [150, {"$date": "2023-05-01T10:00:00Z"}, "Пополнение (cardlink)", "pay_abc123"]

2. Modern object::

       {"amount": 150, "dt": {"$date": ...}, "type": "topup",
        "meta": {"source": "cardlink", "bonus_rub": 0}}

3. Modern object wrapped in a single-element array::

       [{"amount": 150, "dt": ..., "type": "topup", "meta": {...}}]

Every entry is converted into :class:`NormalizedTransaction`.  Debit history
(``logs_balance``: renewals, extra devices, ByPass traffic, gifts) is
normalized into :class:`NormalizedDebit` by the same defensive rules.

Design rules:

* **Never raise** on malformed input — hopeless garbage yields an empty list
  and the caller counts it; partially parseable data is preserved with
  ``kind=UNKNOWN`` and the original entry in ``raw``.
* Dates are always returned timezone-aware (UTC).  Naive datetimes coming
  from pymongo are treated as UTC (that is how Mongo stores them).
* Classification prefers explicit ``type`` fields; the description string is
  only used to *fill gaps*, never to override explicit metadata.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------


class TxKind(str, Enum):
    """Credit-side transaction kinds (money coming *into* user balance)."""

    TOPUP = "topup"              # payment through a provider
    REF_INCOME = "ref_income"    # referral earnings
    PROMO = "promo"              # promo-code balance / promo_balance
    BONUS = "bonus"              # standalone bonus accrual ("+ акция 20%")
    PURCHASE = "purchase"        # historical "Покупка Pro" records kept in transactions
    GIFT = "gift"                # incoming gift
    UNKNOWN = "unknown"


class DebitKind(str, Enum):
    """Debit-side kinds (money spent from balance, from ``logs_balance``)."""

    RENEWAL = "renewal"          # subscription purchase / renewal
    DEVICE = "device"            # extra device slot
    BYPASS = "bypass"            # ByPass traffic packages
    GIFT = "gift"                # gifted subscription to another user
    OTHER = "other"


# Canonical payment sources.  Anything else is lower-cased and kept as-is so
# a brand-new provider shows up in analytics instead of being silently lost.
KNOWN_SOURCES = {
    "cardlink",
    "severpay",
    "wata",
    "heleket",
    "cards_ru",
    "card",
    "stars",
    "crypto",
    "sbp",
    "yookassa",
}

_SOURCE_ALIASES = {
    "card_link": "cardlink",
    "cardlink_ru": "cardlink",
    "sever_pay": "severpay",
    "severpay_ru": "severpay",
    "cards": "cards_ru",
    "cardsru": "cards_ru",
    "карта": "card",
    "картой": "card",
    "telegram_stars": "stars",
    "tg_stars": "stars",
    "звезды": "stars",
    "cryptobot": "crypto",
    "crypto_bot": "crypto",
    "usdt": "crypto",
}


def normalize_source(value: Any) -> Optional[str]:
    """Bring a payment source to its canonical lower-case name."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    s = s.replace("-", "_").replace(" ", "_")
    return _SOURCE_ALIASES.get(s, s)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NormalizedTransaction(BaseModel):
    """A single credit-side transaction in unified form."""

    model_config = ConfigDict(frozen=False)

    amount: float
    dt: Optional[datetime] = None
    kind: TxKind = TxKind.UNKNOWN
    source: Optional[str] = None
    bonus: float = 0.0
    promo_code: Optional[str] = None
    ref_meta: Optional[dict] = None
    payment_id: Optional[str] = None
    desc: Optional[str] = None
    raw: Any = None


class NormalizedDebit(BaseModel):
    """A single debit (spend) from ``logs_balance`` in unified form."""

    model_config = ConfigDict(frozen=False)

    amount: float = Field(description="Absolute spend amount, always >= 0")
    dt: Optional[datetime] = None
    kind: DebitKind = DebitKind.OTHER
    product: Optional[str] = None
    desc: Optional[str] = None
    raw: Any = None


class NormalizedPayment(BaseModel):
    """A provider webhook record from ``payments_webhook`` in unified form.

    This is the provider-side view of a top-up: it carries the commission
    and transaction status which never make it into ``info.transactions``.
    Deliberately NOT merged into transactions_flat revenue (the same top-up
    already exists there) — used for provider health, fees and
    reconciliation.
    """

    model_config = ConfigDict(frozen=False)

    txid: str
    user_id: Optional[int] = None
    dt: Optional[datetime] = None
    amount: float = 0.0
    commission: float = 0.0
    source: Optional[str] = None
    status: str = "other"  # paid | failed | pending | other
    processed: Optional[bool] = None
    tx_type: Optional[str] = None  # e.g. SBP / CARD from the provider payload


# ---------------------------------------------------------------------------
# Scalar parsing helpers
# ---------------------------------------------------------------------------

_MS_THRESHOLD = 1e11  # epoch seconds are ~1.7e9, millis are ~1.7e12


def parse_dt(value: Any) -> Optional[datetime]:
    """Parse a datetime from any historical representation.

    Handles: ``datetime``, ``{"$date": ...}`` (ISO string or
    ``{"$numberLong": millis}``), epoch seconds/milliseconds as int/float/str,
    ISO-8601 strings and a couple of legacy human formats.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, dict):
        if "$date" in value:
            return parse_dt(value["$date"])
        if "$numberLong" in value:
            return parse_dt(value["$numberLong"])
        return None

    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None

    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        ts = value / 1000.0 if value > _MS_THRESHOLD else float(value)
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            try:
                return parse_dt(float(s))
            except ValueError:
                return None
        iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def parse_amount(value: Any) -> Optional[float]:
    """Parse a monetary amount from number / numeric string / extended-JSON."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, dict):
        for key in ("$numberDouble", "$numberInt", "$numberLong", "$numberDecimal"):
            if key in value:
                return parse_amount(value[key])
        return None
    if isinstance(value, str):
        s = value.strip().replace(" ", "").replace(" ", "")
        s = re.sub(r"[₽рp$€]+\.?$", "", s, flags=re.IGNORECASE)
        s = s.replace(",", ".")
        if not s:
            return None
        try:
            f = float(s)
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Description parsing (Russian bot wording, case-insensitive)
# ---------------------------------------------------------------------------

_RE_SOURCE_PAREN = re.compile(r"пополнение[^(«\"]*\(([^)]+)\)", re.IGNORECASE)
_RE_CARD = re.compile(r"пополнение\s+карт", re.IGNORECASE)
_RE_TOPUP = re.compile(r"пополнени", re.IGNORECASE)
_RE_BONUS = re.compile(
    r"\+\s*(?:бонус|акци[яией]{1,2})\s*[:\s]*([\d\s.,]+)\s*₽?", re.IGNORECASE
)
_RE_PROMO = re.compile(
    r"промокод[а-яё]*\s*[«\"']?([A-Za-z0-9_\-А-Яа-яЁё]+)[»\"']?", re.IGNORECASE
)
_RE_REF = re.compile(r"реферал|referral|ref[\._ ]?income|партн[её]р", re.IGNORECASE)
_RE_PURCHASE = re.compile(r"покупка\s+[«\"']?([^»\"'\n]+?)[»\"']?\s*$", re.IGNORECASE)
_RE_GIFT = re.compile(r"подар|дарени", re.IGNORECASE)
# a description that IS a bonus accrual ("Бонус за возвращение (серия)")
_RE_BONUS_WORD = re.compile(r"бонус|акци[яи]|кэшб[еэ]к|cashback", re.IGNORECASE)

# Debit-side wording
_RE_RENEWAL = re.compile(r"продлени|подписк|тариф|subscription|renew|(^|\s)pro(\s|$)", re.IGNORECASE)
_RE_DEVICE = re.compile(r"устройств|слот|device", re.IGNORECASE)
_RE_BYPASS = re.compile(r"bypass|байпас|трафик|гигабайт|\bгб\b|\bgb\b", re.IGNORECASE)


class _DescInfo(BaseModel):
    kind: Optional[TxKind] = None
    source: Optional[str] = None
    bonus: float = 0.0
    promo_code: Optional[str] = None
    product: Optional[str] = None


def parse_description(desc: Optional[str]) -> _DescInfo:
    """Extract kind/source/bonus/promo hints from a free-text description."""
    info = _DescInfo()
    if not desc or not isinstance(desc, str):
        return info
    text = desc.strip()
    if not text:
        return info

    m = _RE_BONUS.search(text)
    if m:
        parsed = parse_amount(m.group(1))
        if parsed is not None:
            info.bonus = parsed

    m = _RE_PROMO.search(text)
    if m:
        info.promo_code = m.group(1)
        info.kind = TxKind.PROMO

    if info.kind is None and _RE_REF.search(text):
        info.kind = TxKind.REF_INCOME

    m = _RE_PURCHASE.search(text)
    if info.kind is None and m:
        info.kind = TxKind.PURCHASE
        info.product = m.group(1).strip()

    m = _RE_SOURCE_PAREN.search(text)
    if m:
        info.source = normalize_source(m.group(1))
        if info.kind is None:
            info.kind = TxKind.TOPUP
    elif _RE_CARD.search(text):
        info.source = "card"
        if info.kind is None:
            info.kind = TxKind.TOPUP
    elif info.kind is None and _RE_TOPUP.search(text):
        info.kind = TxKind.TOPUP

    if info.kind is None and _RE_GIFT.search(text):
        info.kind = TxKind.GIFT

    # "+ акция 40₽" or "Бонус за возвращение" → standalone bonus accrual,
    # NOT provider revenue
    if info.kind is None and (info.bonus or _RE_BONUS_WORD.search(text)):
        info.kind = TxKind.BONUS

    return info


_TYPE_MAP: dict[str, TxKind] = {
    "topup": TxKind.TOPUP,
    "top_up": TxKind.TOPUP,
    "payment": TxKind.TOPUP,
    "deposit": TxKind.TOPUP,
    "ref_income": TxKind.REF_INCOME,
    "referral": TxKind.REF_INCOME,
    "ref": TxKind.REF_INCOME,
    "promo_balance": TxKind.PROMO,
    "promo": TxKind.PROMO,
    "promocode": TxKind.PROMO,
    "bonus": TxKind.BONUS,
    "purchase": TxKind.PURCHASE,
    "gift": TxKind.GIFT,
}

_DEBIT_TYPE_MAP: dict[str, DebitKind] = {
    "renewal": DebitKind.RENEWAL,
    "subscription": DebitKind.RENEWAL,
    "sub": DebitKind.RENEWAL,
    "prolong": DebitKind.RENEWAL,
    "device": DebitKind.DEVICE,
    "extra_device": DebitKind.DEVICE,
    "slot": DebitKind.DEVICE,
    "bypass": DebitKind.BYPASS,
    "traffic": DebitKind.BYPASS,
    "gift": DebitKind.GIFT,
}


# ---------------------------------------------------------------------------
# Credit transactions
# ---------------------------------------------------------------------------


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _from_dict(entry: dict) -> Optional[NormalizedTransaction]:
    amount = parse_amount(_first(entry, "amount", "sum", "value", "rub"))
    dt = parse_dt(_first(entry, "dt", "date", "created_at", "timestamp",
                         "ts", "time"))
    desc = _first(entry, "desc", "description", "details", "text", "title")
    desc = str(desc) if desc is not None else None

    meta = entry.get("meta")
    meta = meta if isinstance(meta, dict) else {}

    type_raw = _first(entry, "type", "kind")
    kind = _TYPE_MAP.get(str(type_raw).strip().lower()) if type_raw else None

    source = normalize_source(_first(meta, "source", "provider", "gateway")
                              or _first(entry, "source", "provider"))
    bonus = parse_amount(_first(meta, "bonus_rub", "bonus", "promo_rub")) or 0.0
    promo_code = _first(meta, "promo_code", "promocode", "code")
    promo_code = str(promo_code) if promo_code is not None else None
    payment_id = _first(meta, "payment_id", "order_id", "invoice_id") \
        or _first(entry, "payment_id", "order_id")
    payment_id = str(payment_id) if payment_id is not None else None

    hints = parse_description(desc)
    if kind is None:
        kind = hints.kind
    if source is None:
        source = hints.source
    if not bonus:
        bonus = hints.bonus
    if promo_code is None:
        promo_code = hints.promo_code

    ref_meta = None
    if kind is TxKind.REF_INCOME:
        ref_meta = meta or (entry.get("ref_meta") if isinstance(entry.get("ref_meta"), dict) else None)
    elif isinstance(entry.get("ref_meta"), dict):
        ref_meta = entry["ref_meta"]
        if kind is None:
            kind = TxKind.REF_INCOME

    if amount is None and dt is None and kind is None:
        return None  # hopeless garbage

    if kind is None:
        # An explicit meta.source strongly implies a provider top-up.
        kind = TxKind.TOPUP if source else TxKind.UNKNOWN

    if kind is TxKind.BONUS and not bonus and (amount or 0) > 0:
        bonus = amount  # standalone accrual: the whole amount is gifted

    return NormalizedTransaction(
        amount=amount if amount is not None else 0.0,
        dt=dt,
        kind=kind,
        source=source,
        bonus=float(bonus or 0.0),
        promo_code=promo_code,
        ref_meta=ref_meta,
        payment_id=payment_id,
        desc=desc,
        raw=entry,
    )


def _from_legacy(entry: list | tuple) -> Optional[NormalizedTransaction]:
    amount = parse_amount(entry[0]) if len(entry) > 0 else None
    dt = parse_dt(entry[1]) if len(entry) > 1 else None
    desc = entry[2] if len(entry) > 2 and entry[2] is not None else None
    desc = str(desc) if desc is not None else None
    payment_id = entry[3] if len(entry) > 3 and entry[3] not in (None, "") else None
    payment_id = str(payment_id) if payment_id is not None else None

    if amount is None and dt is None and desc is None:
        return None

    hints = parse_description(desc)
    kind = hints.kind
    if kind is None:
        # Legacy `info.transactions` records were balance top-ups; a positive
        # amount without any description is treated as a generic top-up.
        kind = TxKind.TOPUP if (amount or 0) > 0 else TxKind.UNKNOWN

    bonus = hints.bonus
    if kind is TxKind.BONUS and not bonus and (amount or 0) > 0:
        bonus = amount  # standalone accrual: the whole amount is gifted

    return NormalizedTransaction(
        amount=amount if amount is not None else 0.0,
        dt=dt,
        kind=kind,
        source=hints.source,
        bonus=bonus,
        promo_code=hints.promo_code,
        ref_meta=None,
        payment_id=payment_id,
        desc=desc,
        raw=list(entry),
    )


def _looks_like_legacy(entry: list | tuple) -> bool:
    """A legacy row starts with a number (or numeric string)."""
    if not entry:
        return False
    head = entry[0]
    if isinstance(head, bool):
        return False
    if isinstance(head, (int, float)):
        return True
    if isinstance(head, str):
        return parse_amount(head) is not None
    return False


def normalize_transaction_entry(entry: Any) -> list[NormalizedTransaction]:
    """Normalize one raw entry from ``info.transactions``.

    Returns a list because format 3 may wrap one *or several* objects in an
    array.  An empty list means the entry was unparseable garbage.
    """
    if entry is None:
        return []

    if isinstance(entry, dict):
        tx = _from_dict(entry)
        return [tx] if tx else []

    if isinstance(entry, (list, tuple)):
        if not entry:
            return []
        if _looks_like_legacy(entry):
            tx = _from_legacy(entry)
            return [tx] if tx else []
        # Wrapped format: array of dicts (usually exactly one).
        out: list[NormalizedTransaction] = []
        for el in entry:
            if isinstance(el, dict):
                tx = _from_dict(el)
                if tx:
                    out.append(tx)
            elif isinstance(el, (list, tuple)):
                out.extend(normalize_transaction_entry(el))
        return out

    return []


def normalize_transactions(raw: Any) -> tuple[list[NormalizedTransaction], int]:
    """Normalize the whole ``info.transactions`` field of a user document.

    Returns ``(transactions, unparsed_count)``.
    """
    if raw is None:
        return [], 0
    if isinstance(raw, dict):
        # Extremely old documents stored {"0": [...], "1": [...]}
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        return [], 1

    result: list[NormalizedTransaction] = []
    unparsed = 0

    # The field itself is a list of entries.  But a *single* wrapped/legacy
    # entry may also be passed directly; normalize_transaction_entry handles
    # both shapes per element.
    for entry in raw:
        txs = normalize_transaction_entry(entry)
        if txs:
            result.extend(txs)
        elif entry is not None:
            unparsed += 1
    return result, unparsed


# ---------------------------------------------------------------------------
# Debits (logs_balance)
# ---------------------------------------------------------------------------


def _debit_kind_from_desc(desc: Optional[str]) -> tuple[DebitKind, Optional[str]]:
    if not desc:
        return DebitKind.OTHER, None
    if _RE_BYPASS.search(desc):
        return DebitKind.BYPASS, None
    if _RE_DEVICE.search(desc):
        return DebitKind.DEVICE, None
    if _RE_GIFT.search(desc):
        return DebitKind.GIFT, None
    m = _RE_PURCHASE.search(desc)
    if m:
        return DebitKind.RENEWAL, m.group(1).strip()
    if _RE_RENEWAL.search(desc):
        return DebitKind.RENEWAL, None
    return DebitKind.OTHER, None


def _debit_from_dict(entry: dict) -> Optional[NormalizedDebit]:
    amount = parse_amount(_first(entry, "amount", "sum", "value", "rub"))
    dt = parse_dt(_first(entry, "dt", "date", "created_at", "timestamp",
                         "ts", "time"))
    desc = _first(entry, "desc", "description", "details", "text", "title",
                  "reason")
    desc = str(desc) if desc is not None else None

    type_raw = _first(entry, "type", "kind")
    kind = _DEBIT_TYPE_MAP.get(str(type_raw).strip().lower()) if type_raw else None
    product = None
    if kind is None:
        kind, product = _debit_kind_from_desc(desc)
    else:
        _, product = _debit_kind_from_desc(desc)

    if amount is None and dt is None and desc is None:
        return None

    meta = entry.get("meta")
    if product is None and isinstance(meta, dict):
        p = _first(meta, "product", "plan", "tariff")
        product = str(p) if p is not None else None

    return NormalizedDebit(
        amount=abs(amount) if amount is not None else 0.0,
        dt=dt,
        kind=kind,
        product=product,
        desc=desc,
        raw=entry,
    )


def _debit_from_legacy(entry: list | tuple) -> Optional[NormalizedDebit]:
    amount = parse_amount(entry[0]) if len(entry) > 0 else None
    dt = parse_dt(entry[1]) if len(entry) > 1 else None
    desc = entry[2] if len(entry) > 2 and entry[2] is not None else None
    desc = str(desc) if desc is not None else None

    if amount is None and dt is None and desc is None:
        return None

    kind, product = _debit_kind_from_desc(desc)
    return NormalizedDebit(
        amount=abs(amount) if amount is not None else 0.0,
        dt=dt,
        kind=kind,
        product=product,
        desc=desc,
        raw=list(entry),
    )


def normalize_debit_entry(entry: Any) -> list[NormalizedDebit]:
    """Normalize one raw entry from ``logs_balance``."""
    if entry is None:
        return []
    if isinstance(entry, dict):
        d = _debit_from_dict(entry)
        return [d] if d else []
    if isinstance(entry, (list, tuple)):
        if not entry:
            return []
        if _looks_like_legacy(entry):
            d = _debit_from_legacy(entry)
            return [d] if d else []
        out: list[NormalizedDebit] = []
        for el in entry:
            if isinstance(el, dict):
                d = _debit_from_dict(el)
                if d:
                    out.append(d)
            elif isinstance(el, (list, tuple)):
                out.extend(normalize_debit_entry(el))
        return out
    return []


_PAID_STATUSES = {"paid", "success", "succeeded", "completed", "confirmed"}
_FAILED_STATUSES = {"failed", "fail", "error", "declined", "canceled",
                    "cancelled", "expired", "rejected"}
_PENDING_STATUSES = {"pending", "created", "waiting", "processing", "new"}


def _normalize_payment_status(value: Any, processed: Any) -> str:
    if value is not None:
        s = str(value).strip().lower()
        if s in _PAID_STATUSES:
            return "paid"
        if s in _FAILED_STATUSES:
            return "failed"
        if s in _PENDING_STATUSES:
            return "pending"
        if s:
            return "other"
    # no explicit status: a processed webhook is a credited payment
    if processed is True:
        return "paid"
    return "other"


def normalize_payment_webhook(doc: dict) -> Optional[NormalizedPayment]:
    """Normalize one ``payments_webhook`` document.

    Only analytics-relevant fields are extracted; PII inside the payload
    (email, order descriptions with user ids) is intentionally dropped.
    """
    if not isinstance(doc, dict):
        return None
    payload = doc.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    txid = doc.get("txid") or payload.get("transactionId") or doc.get("_id")
    if txid is None:
        return None

    amount = parse_amount(_first(doc, "amount_rub", "amount")
                          or payload.get("amount"))
    dt = parse_dt(_first(doc, "created_at", "dt", "date")
                  or payload.get("paymentTime"))
    user_id_raw = doc.get("user_id")
    user_id: Optional[int] = None
    if isinstance(user_id_raw, int) and not isinstance(user_id_raw, bool):
        user_id = user_id_raw
    elif isinstance(user_id_raw, str) and user_id_raw.isdigit():
        user_id = int(user_id_raw)

    processed = doc.get("processed") if isinstance(doc.get("processed"), bool) else None
    return NormalizedPayment(
        txid=str(txid),
        user_id=user_id,
        dt=dt,
        amount=amount if amount is not None else 0.0,
        commission=parse_amount(payload.get("commission")
                                or doc.get("commission")) or 0.0,
        source=normalize_source(doc.get("source") or payload.get("provider")),
        status=_normalize_payment_status(
            payload.get("transactionStatus") or doc.get("status"), processed),
        processed=processed,
        tx_type=str(payload["transactionType"])
        if payload.get("transactionType") is not None else None,
    )


def normalize_debits(raw: Any) -> tuple[list[NormalizedDebit], int]:
    """Normalize the whole ``logs_balance`` field of a user document."""
    if raw is None:
        return [], 0
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        return [], 1

    result: list[NormalizedDebit] = []
    unparsed = 0
    for entry in raw:
        ds = normalize_debit_entry(entry)
        if ds:
            result.extend(ds)
        elif entry is not None:
            unparsed += 1
    return result, unparsed
