"""Сколько ByPass съедает и сколько приносит — на одного человека.

Вопрос «сколько тратит средний пользователь ByPass» на самом деле два
разных, и путать их дорого:

  * **сколько он прокачивает** — это наш расход, гигабайты у хостера;
  * **сколько он платит** — это приход, рубли за пакеты.

Расход берём у панели одним запросом: у ByPass свой сквад, а у сквада есть
ручка расхода по участникам за период. Перебирать подписки по одной здесь
не нужно — это был бы запрос на человека.

Приход берём из журнала баланса: покупки пакетов лежат там с кодом `bypass`
и объёмом в `meta.gb`.

Среднее считается по тем, кто **пользуется**, а не по всем подключившим:
ByPass подключают бесплатно и часто забывают, и «средний расход по всем»
получается втрое меньше настоящего.
"""

from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

GB = 1024 ** 3

FIELDS = {'user_data.user_id': 1, 'vpn.bypass_uuid': 1,
          'vpn.bypass_trafficLimitBytes': 1, 'vpn.bypass_expireAt': 1}


async def collect(users, journal, panel, squad: str, start: datetime,
                  end: datetime) -> dict:
    subscribers, limits = await _subscribers(users)
    traffic, panel_info = await _traffic(panel, squad, subscribers, start, end)
    money = await _money(journal, start, end)

    spenders = sorted((row for row in traffic.items() if row[1] > 0),
                      key=lambda row: -row[1])
    payers = [user_id for user_id, row in money.items() if row['spent'] > 0]

    days = max(1, (end - start).days)
    return {
        'start': start, 'end': end, 'days': days,
        'subscribers': len(subscribers),
        # ── расход
        'used_bytes': sum(value for _, value in spenders),
        'spenders': len(spenders),
        'top': [{'user_id': user_id, 'bytes': value}
                for user_id, value in spenders[:10]],
        'median_bytes': _median([value for _, value in spenders]),
        # ── приход
        'paid': sum(row['spent'] for row in money.values()),
        'payers': len(payers),
        'gb_bought': sum(row['gb'] for row in money.values()),
        'purchases': sum(row['count'] for row in money.values()),
        'median_paid': _median([row['spent'] for row in money.values()
                                if row['spent'] > 0]),
        # Лимит, выданный в панель за всё время: начальный подарок плюс все
        # покупки. Это НЕ остаток — сколько из него съедено, знает панель,
        # а бот своё число только наращивает.
        'limit_bytes': sum(limits.values()),
        'squad': squad,
        'panel': panel_info,
    }


async def _subscribers(users) -> tuple[set[int], dict[int, int]]:
    """Кто вообще подключил ByPass и сколько гигабайт у него на руках."""
    found: set[int] = set()
    limits: dict[int, int] = {}

    async for doc in users.iterate({'vpn.bypass_uuid': {'$nin': ['', None]}}, FIELDS):
        user_id = users.pick(doc, 'user_data.user_id')
        if user_id is None:
            continue
        found.add(int(user_id))
        limits[int(user_id)] = int(users.pick(doc, 'vpn.bypass_trafficLimitBytes', 0) or 0)

    return found, limits


def telegram_id(key) -> int | None:
    """Telegram id из ключа расхода — или None, если это не он.

    У ByPass своя запись в панели, и зовут её `<id>_bypass`: под этим
    именем создаётся подписка. Первая версия отчёта брала только ключи из
    одних цифр — и отбрасывала ровно всех, кого считала. Отчёт показывал
    честный ноль там, где трафик шёл терабайтами.
    """
    text = str(key)
    if text.isdigit():
        return int(text)
    head, _, tail = text.partition('_')
    if tail == 'bypass' and head.isdigit():
        return int(head)
    return None


async def _traffic(panel, squad: str, subscribers: set[int],
                   start: datetime, end: datetime) -> tuple[dict[int, int], dict]:
    """Расход по скваду ByPass: {telegram id: байты} и что ответила панель.

    Второй словарь нужен, чтобы пустой результат было чем объяснить: «панель
    промолчала», «ответила, но никого из наших в ответе нет» и «никто не
    качал» — три разные беды, а на экране они выглядели одинаково.
    """
    info = {'rows': 0, 'sample': [], 'error': '', 'asked': bool(squad)}
    if not squad:
        info['error'] = 'сквад ByPass не задан'
        return {}, info

    try:
        usage = await panel.squad_usage(squad, start, end)
    except Exception as exc:                     # noqa: BLE001 — отчёт, не платёж
        log.warning('расход сквада ByPass не получен: %s', exc)
        info['error'] = str(exc)
        return {}, info

    usage = usage or {}
    info['rows'] = len(usage)
    info['sample'] = [str(key) for key in list(usage)[:3]]

    found: dict[int, int] = {}
    for key, value in usage.items():
        user_id = telegram_id(key)
        if user_id is None or user_id not in subscribers:
            continue
        # Панель отдаёт одного человека дважды: под своим числовым id и под
        # username. Берём большее — оба числа про один и тот же расход, а
        # складывать их значило бы удвоить его.
        found[user_id] = max(found.get(user_id, 0), int(value or 0))
    return found, info


async def _money(journal, start: datetime, end: datetime) -> dict[int, dict]:
    """Покупки пакетов за период: сколько рублей и гигабайт на человека."""
    rows: dict[int, dict] = {}

    async for row in journal.iterate(
            {'kind': 'bypass', 'at': {'$gte': start, '$lte': end}},
            {'user_id': 1, 'amount': 1, 'meta': 1, 'at': 1}):
        user_id = row.get('user_id')
        if user_id is None:
            continue
        entry = rows.setdefault(int(user_id), {'spent': 0, 'gb': 0, 'count': 0})
        # Списание лежит со знаком минус: это расход человека, а для нас приход.
        entry['spent'] += abs(int(row.get('amount') or 0))
        entry['gb'] += int((row.get('meta') or {}).get('gb') or 0)
        entry['count'] += 1
    return rows


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def gb(value: int) -> float:
    return round(int(value or 0) / GB, 1)


def per_month(value: float, days: int) -> float:
    """Привести к месяцу: отчёт зовут и за неделю, и за квартал."""
    return round(value * 30 / max(1, days), 1)
