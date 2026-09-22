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
WAS_CUSTOMER = 'друг покупал ещё до журнала'
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


# Слова, по которым в старых транзакциях узнаётся покупка подписки.
# Журнал (balance_log) ведётся с того дня, как его завели, а люди покупают
# дольше: у пришедшего два года назад его первая покупка в журнал не попала
# вовсе. Проверять «новый ли друг» по одному журналу значит считать новыми
# всех старых — поэтому вторая проверка идёт по info.transactions, которые
# писал ещё прежний бот.
PURCHASE_WORDS = ('покупка подписки', 'продление подписки', 'тариф')


def bought_before(transactions, moment: datetime) -> bool:
    """Покупал ли человек подписку раньше этого момента — по его карточке."""
    from app.core.time import to_msk
    from app.domain.transactions import extract

    moment = to_msk(moment)
    for row in transactions or []:
        amount, at, description = extract(row)
        if at is None or at >= moment:
            continue
        # Списание: покупка — это минус на балансе. Плюс с тем же словом
        # («Возврат: покупка…») покупкой не является.
        if amount is None or float(amount) >= 0:
            continue
        text = str(description or '').lower()
        if any(word in text for word in PURCHASE_WORDS):
            return True
    return False


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


# ── статистика билетов ──────────────────────────────────────────────────────
#
# Сводка отвечает на «сколько всего», статистика — на «как идёт и у кого».
# Это разные вопросы: акция с тысячей билетов у пятнадцати человек и та же
# тысяча у четырёхсот — это два разных результата, а сумма у них одна.

BUCKETS = ((1, 1, '1 билет'), (2, 3, '2–3'), (4, 9, '4–9'),
           (10, 10 ** 9, '10 и больше'))


def stats(tickets: list[dict], participants: list[dict]) -> dict:
    """Распределение билетов: по дням, по людям и по источнику."""
    total = len(tickets)
    people = len(participants)

    by_day: dict[str, dict] = {}
    seen: set[int] = set()
    for row in sorted(tickets, key=lambda item: item['at']):
        day = row['at'].strftime('%d.%m')
        entry = by_day.setdefault(day, {'day': day, 'tickets': 0, 'people': 0})
        entry['tickets'] += 1
        if row['owner'] not in seen:
            seen.add(row['owner'])
            entry['people'] += 1

    counts = sorted((item['tickets'] for item in participants), reverse=True)
    buckets = [{'label': label,
                'people': sum(1 for value in counts if low <= value <= high)}
               for low, high, label in BUCKETS]

    friends = sum(1 for row in tickets if row['kind'] == FRIEND)

    return {
        'total': total,
        'people': people,
        'average': round(total / people, 1) if people else 0.0,
        'median': _median(counts),
        'most': counts[0] if counts else 0,
        'days': list(by_day.values()),
        'buckets': buckets,
        'friends': friends,
        'own': total - friends,
        # Сколько человек привёл хотя бы одного друга: главное число акции
        # «приведи друга» — остальные просто продлились.
        'inviters': sum(1 for item in participants if item.get('friends')),
    }


def _median(sorted_desc: list[int]) -> int:
    """Медиана: половина участников имеет столько билетов или меньше.

    Нужна рядом со средним, потому что среднее врёт: один человек с сотней
    билетов поднимает его всем, и «в среднем четыре» превращается в ответ
    ни о чём.
    """
    if not sorted_desc:
        return 0
    middle = len(sorted_desc) // 2
    return sorted_desc[middle]
