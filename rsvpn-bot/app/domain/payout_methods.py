"""Способы вывода реферального баланса — описание и проверки.

Перенос из utils.py (make_method_default_payload, method_title,
format_method_details, draft_is_ready, REQUIRED_FIELDS, FIELD_TITLES).

Всё собрано в таблицу METHODS: добавить новый способ выплаты — значит
дописать одну запись, а не править пять функций с `if mtype == ...`.
Модуль чистый: ни БД, ни aiogram, поэтому маскирование карты и проверка
заполненности проверяются тестом мгновенно.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    code: str
    title: str
    hint: str = ''


@dataclass(frozen=True)
class MethodType:
    code: str
    title: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


METHODS: tuple[MethodType, ...] = (
    MethodType('sbp', 'СБП', (
        Field('fio', 'ФИО', 'Полностью, как в банке: Иванов Иван Иванович'),
        Field('phone', 'Телефон', 'Номер, привязанный к СБП: +79001234567'),
        Field('bank', 'Банк', 'Куда зачислять: Сбербанк, Т-Банк, Альфа…'),
    )),
    MethodType('mir', 'МИР', (
        Field('fio', 'ФИО', 'Держатель карты полностью'),
        Field('card', 'Номер карты', '16 цифр, можно с пробелами'),
    )),
    MethodType('crypto', 'Криптовалюта', (
        Field('wallet', 'Номер кошелька', 'Адрес USDT (TRC-20)'),
    )),
)

BY_CODE: dict[str, MethodType] = {m.code: m for m in METHODS}
DEFAULT_TYPE = 'sbp'

# Куда выплачивать, если человек не добавил ни одного способа
BOT_BALANCE = 'bot_balance'
BOT_BALANCE_TITLE = '👤 На баланс бота'


def method_title(method: dict | None) -> str:
    known = BY_CODE.get((method or {}).get('type', ''))
    return known.title if known else ((method or {}).get('title') or 'Способ')


def fields_of(type_code: str) -> tuple[Field, ...]:
    known = BY_CODE.get(type_code)
    return known.fields if known else ()


def empty_data(type_code: str) -> dict[str, str]:
    return {f.code: '' for f in fields_of(type_code)}


def new_draft(type_code: str = DEFAULT_TYPE) -> dict:
    return {'type': type_code, 'data': empty_data(type_code)}


def missing_fields(draft: dict | None) -> list[Field]:
    """Какие поля ещё не заполнены. Пустой список — можно сохранять."""
    data = (draft or {}).get('data') or {}
    return [f for f in fields_of((draft or {}).get('type', ''))
            if not str(data.get(f.code) or '').strip()]


def is_ready(draft: dict | None) -> bool:
    return bool((draft or {}).get('type')) and not missing_fields(draft)


def mask_card(value: str) -> str:
    """612345******7890 — в админ-чат и на экран не должен уезжать полный номер."""
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) < 12:
        return str(value or '')
    return f'{digits[:6]}******{digits[-4:]}'


def format_value(field_code: str, value: str) -> str:
    return mask_card(value) if field_code == 'card' else (str(value or '').strip() or 'Не указано')


def format_details(method: dict | None, mask: bool = True) -> str:
    """Человекочитаемая карточка способа.

    mask=False — для админа, который должен видеть полный номер карты,
    чтобы выплатить. Везде остальное (экраны пользователя, логи) — с маской.
    """
    type_code = (method or {}).get('type', '')
    data = (method or {}).get('data') or {}
    known = BY_CODE.get(type_code)
    if not known:
        return 'Неизвестный тип способа.'

    lines = [f'<b>Тип:</b> <code>{known.title}</code>']
    for f in known.fields:
        raw = str(data.get(f.code) or '').strip()
        shown = (format_value(f.code, raw) if mask else (raw or 'Не указано'))
        lines.append(f'• <b>{f.title}:</b> <code>{shown}</code>')
    return '\n'.join(lines)


def find(methods: list[dict] | None, method_id: str) -> dict | None:
    return next((m for m in (methods or [])
                 if isinstance(m, dict) and m.get('id') == method_id), None)


def selected_title(methods: list[dict] | None, selected: str) -> str:
    if selected == BOT_BALANCE or not selected:
        return BOT_BALANCE_TITLE
    found = find(methods, selected)
    return method_title(found) if found else BOT_BALANCE_TITLE
