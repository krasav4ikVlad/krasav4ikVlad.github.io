"""Отпечаток плательщика: чем платили, одной строкой.

Провайдеры кладут в ответ разные поля — где-то почта, где-то маска карты,
где-то телефон. Собираем всё, что нашлось: совпадение хотя бы по одному
означает, что за двух разных людей платили с одного кошелька.

Само по себе это не обвинение. Один человек оплачивает подписку жене и
родителям — и это нормальный, хороший клиент. Но он же выглядит как
десяток «новичков», каждый из которых получает бонус за первую оплату, и
разница между этими двумя случаями видна только глазами.
"""

from __future__ import annotations

PRINT_KEYS = ('email', 'payer_email', 'buyer_email', 'customer_email',
              'phone', 'payer_phone', 'card', 'card_mask', 'pan',
              'account', 'payer_id', 'wallet')


def fingerprint(payload) -> str:
    """Значения приводим к нижнему регистру: один и тот же адрес приходит
    от провайдеров то так, то иначе, и без этого совпадение теряется."""
    if not isinstance(payload, dict):
        return ''

    parts = []
    for key in PRINT_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            parts.append(f'{key}={str(value).strip().lower()}')
    return '|'.join(parts)
