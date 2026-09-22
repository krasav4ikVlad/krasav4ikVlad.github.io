"""Розыгрыш: что даёт билет и как билеты нумеруются.

Источников два, и оба про подписку, а не про деньги на балансе:

  * **новый приглашённый друг** купил подписку от месяца — три билета
    тому, кто его привёл. За повторные покупки того же друга не даётся
    ничего: иначе билеты набирались бы на одном человеке;
  * **своя покупка** — билет за каждый месяц.

Пополнение баланса билетов не даёт вовсе. Это не придирка: деньги на
балансе ничего не стоят, пока не превратились в подписку, а розыгрыш
должен вознаграждать второе.

«Новый» — тоже не придирка. Если считать любую покупку друга, то человек с
пятьюдесятью старыми друзьями получит билеты за их обычные продления,
ничего для этого не сделав, а тот, кто привёл пятерых новых, — меньше.

Здесь только правила и ни одного обращения к базе: их надо было записать
один раз так, чтобы текст в канале и таблица для розыгрыша считали одно и
то же.
"""

from __future__ import annotations

import random
import re
from datetime import datetime

from app.core.time import MSK, parse_dt

# ── источники билета ────────────────────────────────────────────────────────
FRIEND = 'друг'
SELF = 'своя подписка'

# ── почему покупка не даёт билета ───────────────────────────────────────────
NO_REFERRER = 'без пригласившего'
SELF_INVITE = 'сам себя пригласил'
UNKNOWN_REFERRER = 'пригласившего нет в базе'
TOO_SHORT = 'подписка меньше месяца'
NOT_NEW = 'друг покупал и раньше'
NOT_ACTIVE = 'подписка не активна'


def parse_day(text: str, end: bool = False) -> datetime | None:
    """«22.09.2026» или «2026-09-22» → начало (или конец) этого дня в МСК.

    Конец дня, а не полночь: «акция до 28 сентября» в понимании человека
    включает всё 28 сентября, и покупка в 23:50 обязана попасть в зачёт.
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

    return parse_dt(raw)


def number_tickets(events: list[dict]) -> list[dict]:
    """Развернуть события в билеты и пронумеровать по дате.

    Один билет — одна строка, даже если событие дало три: розыгрыш идёт по
    номерам, и «билет №17» должен означать ровно одного человека. Иначе
    пришлось бы объяснять, как из строки с тремя билетами выбирается
    выигравший, — а это ровно то место, где теряется доверие.

    Порядок — по дате покупки: номер билета зависит от того, что уже
    произошло, а не от того, как таблицу отсортировали перед выгрузкой.
    """
    good = sorted((row for row in events if row.get('valid')),
                  key=lambda row: (row['at'], row['owner'], row.get('friend') or 0))

    tickets: list[dict] = []
    for row in good:
        for _ in range(max(0, int(row.get('tickets') or 0))):
            tickets.append({
                'ticket': len(tickets) + 1,
                'at': row['at'],
                'owner': row['owner'],
                'owner_username': row.get('owner_username') or '',
                'kind': row['kind'],
                'detail': row.get('detail') or '',
                'friend': row.get('friend') or 0,
            })
    return tickets


# ── призы и жребий ──────────────────────────────────────────────────────────
#
# Розыгрыш внутри бота проверяемым не сделать никаким кодом: снаружи видно
# только результат. Проверяемым его делает порядок действий — список
# билетов публикуется до жребия, и любой может пересчитать, что победивший
# номер в нём был и принадлежал этому человеку. Поэтому жребий здесь
# сохраняется один раз: повторный бросок, из которого выбирают
# понравившийся, — это уже не розыгрыш.

PRIZE_COUNT = re.compile(r'^(.*?)[\s]*[x×*]\s*(\d+)$', re.IGNORECASE)


def parse_prizes(text: str) -> list[str]:
    """«iPhone 18 Pro / 5000₽ x10» → список призов по одному на победителя.

    Список нужен именно развёрнутым: победителей столько же, сколько
    призов, и каждому в отчёте пишется его приз, а не номер строки.
    """
    prizes: list[str] = []
    for line in (text or '').splitlines():
        name = line.strip()
        if not name:
            continue
        match = PRIZE_COUNT.match(name)
        if match and match.group(1).strip():
            prizes.extend([match.group(1).strip()] * min(int(match.group(2)), 500))
        else:
            prizes.append(name)
    return prizes


def draw(tickets: list[dict], count: int, rng=None) -> list[dict]:
    """Вытащить `count` билетов. Один человек выигрывает не больше раза.

    Шанс пропорционален числу билетов — тащим билет, а не участника.
    Выигравший выбывает вместе со всеми своими билетами: иначе человек с
    сотней билетов забрал бы половину призов, и это выглядело бы как
    подтасовка, чем бы оно ни было на самом деле.
    """
    if not tickets or count <= 0:
        return []

    picker = rng or random.SystemRandom()
    pool = list(tickets)
    winners: list[dict] = []

    while pool and len(winners) < count:
        row = pool[picker.randrange(len(pool))]
        winners.append(row)
        owner = row['owner']
        pool = [item for item in pool if item['owner'] != owner]

    return winners
