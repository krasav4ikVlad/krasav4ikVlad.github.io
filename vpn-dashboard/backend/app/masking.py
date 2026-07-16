"""PII masking. The API must never expose payout requisites in the clear:
card numbers, phone numbers, emails, full names."""

from __future__ import annotations

import re
from typing import Any

_RE_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_RE_CARD = re.compile(r"\b(?:\d[ -]?){12,18}(\d{4})\b")
_RE_PHONE = re.compile(r"(?<!\d)(\+?[78][ -]?[\d( -]{8,14})(\d{2})(?!\d)")

# dict keys whose string values are always fully treated as requisites
_SENSITIVE_KEYS = {
    "card", "card_number", "cardnumber", "phone", "phone_number", "email",
    "fio", "full_name", "fullname", "name", "requisites", "details",
    "account", "iban", "wallet", "address", "bank", "card_holder", "holder",
}


def mask_email(value: str) -> str:
    def repl(m: re.Match) -> str:
        user, domain = m.group(1), m.group(2)
        return f"{user[0]}***@{domain[0]}***.{domain.rsplit('.', 1)[-1]}"
    return _RE_EMAIL.sub(repl, value)


def mask_card(value: str) -> str:
    return _RE_CARD.sub(lambda m: f"•••• {m.group(1)}", value)


def mask_phone(value: str) -> str:
    return _RE_PHONE.sub(lambda m: f"+{'•' * 8}{m.group(2)}", value)


def mask_free_text(value: str) -> str:
    """Mask any requisite-looking substrings inside free text."""
    return mask_phone(mask_card(mask_email(value)))


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        s = mask_free_text(value)
        # if regexes didn't catch anything (e.g. a bare name), blunt-mask
        if s == value and value.strip():
            head = value.strip()[0]
            s = f"{head}***"
        return s
    return "***"


def mask_payout_entry(entry: Any) -> Any:
    """Recursively mask requisites in a payout-history entry or dict."""
    if isinstance(entry, dict):
        out = {}
        for key, value in entry.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                out[key] = _mask_value(value)
            else:
                out[key] = mask_payout_entry(value)
        return out
    if isinstance(entry, list):
        return [mask_payout_entry(v) for v in entry]
    if isinstance(entry, str):
        return mask_free_text(entry)
    return entry
