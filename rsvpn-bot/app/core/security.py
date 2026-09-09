"""Проверка подписей вебхуков.

Отдельно от FastAPI: подпись — это не транспорт, а правило безопасности,
и его нужно уметь проверять в тестах без поднятия веб-сервера.
"""

from __future__ import annotations

import hashlib
import hmac


def hmac_sha256_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_hmac_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """True — подпись сошлась.

    Пустой секрет означает «проверка не настроена»: панель шлёт без подписи,
    отклонять нечего. Это осознанный компромисс, а не забытая проверка —
    в логе такой запрос виден по note=bad_signature только при заданном секрете.
    """
    if not secret:
        return True
    if not signature:
        return False

    expected = hmac_sha256_hex(secret, body)
    if hmac.compare_digest(signature, expected):
        return True
    # некоторые версии панели шлют с префиксом алгоритма
    if signature.startswith('sha256='):
        return hmac.compare_digest(signature.removeprefix('sha256='), expected)
    return False
