"""Розыгрыш: что считается билетом и как билеты нумеруются.

Билет — не «приглашённый», а **приглашённый, который впервые заплатил
настоящими деньгами в период акции**. Всё остальное накручивается бесплатно:
регистрация ничего не стоит, бесплатный период ничего не стоит, промокод
ничего не стоит. Розыгрыш, где билет даётся за регистрацию, разыгрывает
телефон среди пустых аккаунтов.

«Впервые» — тоже не придирка. Если считать любую оплату друга, то человек с
пятьюдесятью старыми друзьями получает пятьдесят билетов за их обычные
продления, ничего для этого не сделав, а тот, кто привёл пятерых новых, —
пять. Акция должна платить за новых платящих, иначе она не приносит денег.

Здесь только правила и ни одного обращения к базе: их надо было записать
один раз так, чтобы текст в канале и таблица для розыгрыша считали одно и
то же.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.time import MSK, parse_dt

# ── почему оплата не даёт билета ────────────────────────────────────────────
NO_REFERRER = 'без пригласившего'
SELF_INVITE = 'сам себя пригласил'
UNKNOWN_REFERRER = 'пригласившего нет в базе'
TOO_SMALL = 'оплата меньше минимальной'
NOT_ACTIVE = 'подписка не активна'

# ── подозрения ──────────────────────────────────────────────────────────────
#
# Не приговор: билет остаётся в таблице с пометкой. Решение — за человеком,
# потому что каждый из признаков бывает и у честного участника: друзья
# заплатили в один вечер, потому что он собрал их в одном чате.
BATCH = 'пачка оплат'
SAME_PAYER = 'один плательщик'
NEVER_CONNECTED = 'ни разу не подключался'

# Сколько оплат одного пригласившего в каком окне считаем пачкой.
BATCH_SIZE = 3
BATCH_WINDOW = timedelta(hours=1)

# Из чего собираем отпечаток плательщика. Ключи разные у разных провайдеров,
# поэтому берём все, что встречаются, — совпадение хотя бы по одному уже
# означает, что за двух разных людей платили с одного кошелька.
PRINT_KEYS = ('email', 'payer_email', 'buyer_email', 'customer_email',
              'phone', 'payer_phone', 'card', 'card_mask', 'pan',
              'account', 'payer_id', 'wallet')


def parse_day(text: str, end: bool = False) -> datetime | None:
    """«01.10.2026» или «2026-10-01» → начало (или конец) этого дня в МСК.

    Конец дня, а не полночь: «акция до 22 октября» в понимании человека
    включает всё 22 октября, и оплата в 23:50 обязана попасть в зачёт.
    """
    raw = (text or '').strip()
    if not raw:
        return None

    for pattern in ('%d.%m.%Y', '%Y-%m-%d', '%d.%m.%y'):
        try:
            day = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return day.replace(hour=23, minute=59, second=59, tzinfo=MSK) if end \
            else day.replace(hour=0, minute=0, second=0, tzinfo=MSK)

    parsed = parse_dt(raw)
    return parsed


def fingerprint(payload) -> str:
    """Чем платили — одной строкой, чтобы заметить один кошелёк на многих.

    Значения приводим к нижнему регистру: один и тот же адрес приходит от
    провайдеров то так, то иначе, и без этого совпадение теряется.
    """
    if not isinstance(payload, dict):
        return ''

    parts = []
    for key in PRINT_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            parts.append(f'{key}={str(value).strip().lower()}')
    return '|'.join(parts)


def mark_batches(rows: list[dict]) -> None:
    """Пометить пачки: много оплат одного пригласившего в пределах часа.

    Считаем по скользящему окну, а не по «все оплаты за день»: честный
    участник приводит друзей неделю, накрутчик — за один вечер.
    """
    by_owner: dict[int, list[dict]] = {}
    for row in rows:
        if row.get('referrer_id'):
            by_owner.setdefault(row['referrer_id'], []).append(row)

    for group in by_owner.values():
        group.sort(key=lambda row: row['at'])
        for index, row in enumerate(group):
            window = [other for other in group[index:]
                      if other['at'] - row['at'] <= BATCH_WINDOW]
            if len(window) >= BATCH_SIZE:
                for hit in window:
                    add_flag(hit, BATCH)


def mark_same_payer(rows: list[dict]) -> None:
    """Пометить один и тот же кошелёк у разных аккаунтов."""
    by_print: dict[str, list[dict]] = {}
    for row in rows:
        if row.get('print'):
            by_print.setdefault(row['print'], []).append(row)

    for group in by_print.values():
        if len({row['friend_id'] for row in group}) > 1:
            for row in group:
                add_flag(row, SAME_PAYER)


def add_flag(row: dict, flag: str) -> None:
    flags = row.setdefault('flags', [])
    if flag not in flags:
        flags.append(flag)


def number(rows: list[dict]) -> list[dict]:
    """Пронумеровать билеты по дате оплаты — от первой к последней.

    Порядок именно такой, потому что по нему потом разыгрывают: номер билета
    должен зависеть от того, что уже произошло, а не от того, как таблицу
    отсортировали перед выгрузкой.
    """
    rows.sort(key=lambda row: (row['at'], row['friend_id']))
    ticket = 0
    for row in rows:
        if row.get('valid'):
            ticket += 1
            row['ticket'] = ticket
        else:
            row['ticket'] = 0
    return rows
