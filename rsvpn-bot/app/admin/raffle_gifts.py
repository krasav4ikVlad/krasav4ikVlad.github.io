"""Итоги розыгрыша: публичный файл билетов, месяцы подписки и письма.

Три команды, которые нужны уже после жребия, когда призы розданы и пора
объявлять:

  * `/rafflepublic` — файл билетов для публикации: номер, дата, время и
    закрытый id. Полных id в нём нет вовсе, поэтому его можно выложить в
    канал, а не рассказывать словами, что список «где-то есть»;
  * `/rafflemonth` — раздать месяц подписки случайным участникам. Тянется
    так же, как призы: шанс пропорционален билетам, один человек получает
    не больше одного месяца;
  * `/rafflewinners` — записать победителей, определённых не ботом: числа
    чаще тянут генератором на видео, а итог ведут в таблице. Боту он
    нужен, чтобы знать, кому писать;
  * `/rafflenote` — письмо победителям. Сначала показывает, что уйдёт, и
    кому, и только по второй команде отправляет.

Почему раздача и письма разделены
─────────────────────────────────
Письмо говорит «подписка уже продлена», и это должно быть правдой в тот
момент, когда человек его читает. Поэтому `/rafflenote месяц` не отправит
ничего, пока месяцы не начислены: сначала выдача, потом объявление, а не
наоборот.

Почему всё с подтверждением
───────────────────────────
Обе команды необратимы по-разному: начисление ещё можно отменить руками,
а отправленное письмо — уже нет. Поэтому первый вызов всегда показывает
список и текст, а делает — только второй, со словом «выдать» или
«отправить».
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router, types
from aiogram.filters import Command

from app.admin.raffle import USAGE, NO_DATES, filename, period, report, send_table
from app.campaigns.sender import Sender
from app.content import ids, texts
from app.content.emoji import e
from app.core import db as names
from app.core.time import MSK, fmt, now, parse_dt
from app.domain import raffle as domain
from app.services import raffle_prizes as prizes

log = logging.getLogger(__name__)

# Столбцы публичного файла: ни полного id, ни юзернейма. Всё, что нужно
# для проверки жребия, — номер билета и время его появления.
PUBLIC_COLUMNS = ('билет', 'дата', 'время', 'участник_id_скрытый')

MONTH_DAYS = 30
MONTH_PRIZE = 'Месяц подписки'
DEFAULT_MONTHS = 25

PUBLIC_USAGE = (
    f'{e("document")} <b>Файл билетов для публикации</b>\n\n'
    f'<code>/rafflepublic</code> — все билеты за акцию\n'
    f'<code>/rafflepublic 02.10.2026 23:00</code> — только до этого момента\n'
    f'<code>/rafflepublic 02.10.2026</code> — по конец этого дня\n'
    f'<code>/rafflepublic 7042</code> — по этот номер билета включительно\n'
    f'<code>/rafflepublic снимок</code> — из выгруженного ранее списка\n\n'
    f'<blockquote>Время в боте всегда московское, независимо от часов '
    f'сервера: что показано в файле, то и считается. Отсечка нужна, когда '
    f'приём билетов закончился раньше, чем вы собрали файл.\n\n'
    f'Номера считаются живьём. Слово «снимок» берёт их из выгрузки '
    f'<code>/raffletickets</code> — это нужно, только если тот список уже '
    f'лежит в канале и номера в нём обещаны людям.</blockquote>'
)

MONTH_USAGE = (
    f'{e("calendar")} <b>Месяц подписки случайным участникам</b>\n\n'
    f'<code>/rafflemonth {DEFAULT_MONTHS}</code> — вытащить '
    f'{DEFAULT_MONTHS} человек и показать список\n'
    f'<code>/rafflemonth выдать</code> — начислить им по '
    f'{MONTH_DAYS} дней\n'
    f'<code>/rafflemonth {DEFAULT_MONTHS} заново</code> — пересдать\n\n'
    f'<blockquote>Тянется из билетов, а не из списка участников: у кого '
    f'билетов вдвое больше, у того и шанс вдвое выше. Один человек '
    f'получает не больше одного месяца.\n\nСписок сохраняется: '
    f'<code>/rafflemonth</code> без аргументов покажет тот же самый, а '
    f'выдача пойдёт ровно по нему.</blockquote>'
)

NOTE_USAGE = (
    f'{e("envelope")} <b>Письмо победителям</b>\n\n'
    f'<code>/rafflenote</code> — что уйдёт победителям жребия и кому\n'
    f'<code>/rafflenote месяц</code> — то же для получателей месяца\n'
    f'<code>/rafflenote отправить</code> — разослать\n'
    f'<code>/rafflenote месяц отправить</code> — разослать получателям '
    f'месяца\n\n'
    f'<blockquote>Текст правится в /admin → Тексты: «Письмо победителю» и '
    f'«Письмо о месяце подписки».</blockquote>'
)


# ── публичный файл билетов ──────────────────────────────────────────────────
# Слова управления командой — не даты: их надо убрать до разбора отсечки,
# иначе «снимок» прилетает в парсер дня и команда отвечает «не понял».
WORDS = ('снимок', 'снимка', 'snapshot')


def cutoff(args: str) -> tuple[datetime | None, str]:
    """«02.10.2026 23:00» → момент отсечки. Возвращает (момент, ошибка).

    Время московское — то же, что в самом файле: пересчитывать его в часы
    сервера не нужно, бот живёт по Москве в любой таймзоне машины.
    """
    parts = [word for word in (args or '').split()
             if word.lower() not in WORDS and not word.isdigit()]
    if not parts:
        return None, ''

    day = domain.parse_day(parts[0], end=True)
    if not day:
        return None, parts[0]
    if len(parts) == 1:
        return day, ''

    try:
        clock = datetime.strptime(parts[1], '%H:%M')
    except ValueError:
        return None, parts[1]
    return day.replace(hour=clock.hour, minute=clock.minute, second=0,
                       microsecond=0, tzinfo=MSK), ''


def public_rows(rows: list[dict]) -> list[list]:
    """Строки публичного файла: номер, дата, время и закрытый id."""
    return [[row['ticket'],
             fmt(row['at'], '%d.%m.%Y'),
             fmt(row['at'], '%H:%M:%S'),
             ids.public(row['owner'])] for row in rows]


def until(rows: list[dict], moment: datetime | None) -> list[dict]:
    """Билеты по момент включительно. Нумерация при этом не едет: билеты
    пронумерованы по времени покупки, и отсекается ровно хвост."""
    if not moment:
        return list(rows)
    return [row for row in rows if (parse_dt(row['at']) or now()) <= moment]


def last_number(args: str) -> int:
    """Голое число в аргументах — это номер последнего билета.

    Отсечку по времени сначала считают глазами («до полуночи по Москве —
    это 23:00 у нас»), и ошибиться в ней легче, чем в номере: номер видно
    в предыдущем файле. Поэтому можно сказать и так.
    """
    return next((int(word) for word in (args or '').split()
                 if word.isdigit()), 0)


def up_to(rows: list[dict], number: int) -> list[dict]:
    """Билеты по номер включительно."""
    if number <= 0:
        return list(rows)
    return [row for row in rows if int(row['ticket']) <= number]


async def public(message: types.Message, command, c, settings) -> None:
    """`/rafflepublic` — файл билетов, который не страшно выложить."""
    moment, bad = cutoff(command.args or '')
    if bad:
        await message.answer(
            f'{e("cross")} Не понял «<code>{bad}</code>».\n\n' + PUBLIC_USAGE)
        return

    data = await report(message, _no_args(command), c, settings)
    if data is None:
        return

    from app.admin.raffle import published

    # Снимок — это выгрузка `/raffletickets`, сделанная когда-то раньше.
    # Для файла, который публикуют сейчас, он подходит только если список
    # уже выложен и номера в нём обещаны людям. Поэтому по умолчанию —
    # пересчёт живьём, а снимок берётся словом: иначе команда молча отдаёт
    # список недельной давности, и это заметно уже по дате в файле.
    snap = await published(c, data)
    old = any(word in (command.args or '').lower() for word in WORDS)
    rows = (snap.get('rows') or []) if (old and snap) else data['rows']
    if old and not snap:
        await message.answer(f'{e("cross")} Снимка нет: список ещё не '
                             f'выгружали командой <code>/raffletickets</code>.')
        return

    number = last_number(command.args or '')
    taken = up_to(until(rows, moment), number)
    if not taken:
        await message.answer(f'{e("cross")} До этой отсечки билетов нет.')
        return

    cut = len(rows) - len(taken)
    note = ''
    if moment:
        note += (f'\n{e("clock")} Отсечка: <b>{fmt(moment)}</b> по Москве.')
    if number:
        note += f'\n{e("pin")} Последний билет: <b>№{number}</b>.'
    if cut:
        note += f'\n{e("trash")} Отброшено после отсечки: <b>{cut}</b>.'
    await send_table(
        message, data, what='raffle-public', columns=PUBLIC_COLUMNS,
        rows=public_rows(taken), sheet='Билеты',
        caption=(
            f'{e("document")} Билетов в файле: <b>{len(taken)}</b>{note}\n'
            + source_note(snap, old=old, total=len(rows)) + '\n\n'
            f'<blockquote>Полных id в файле нет: только первые три цифры и '
            f'последние две. Свою строку человек найдёт, чужой id из файла '
            f'не набрать.\n\nВремя московское — то же, что видят люди в '
            f'боте.</blockquote>'))


def source_note(snap: dict | None, *, old: bool, total: int) -> str:
    """Откуда взяты номера. Молчать об этом нельзя: снимок и живой пересчёт
    дают разные номера, а в посте с итогами стоят конкретные числа."""
    if old and snap:
        return (f'{e("clock")} Номера из снимка от <b>{fmt(snap["at"])}</b> '
                f'({snap.get("total", 0)} билетов) — того, что уже выложен.')

    line = f'{e("refresh")} Номера пересчитаны живьём: всего <b>{total}</b>.'
    if snap:
        line += (f'\n{e("warning")} Есть снимок от <b>{fmt(snap["at"])}</b> '
                 f'на {snap.get("total", 0)} билетов — он старше. Если в '
                 f'канале уже лежит он, берите <code>/rafflepublic '
                 f'снимок</code>, иначе номера в файле и в посте разойдутся.')
    return line


# ── месяц подписки случайным ────────────────────────────────────────────────
def month_key(data: dict) -> str:
    return (f'month-{fmt(data["start"], "%Y%m%d")}'
            f'-{fmt(data["end"], "%Y%m%d")}')


def month_text(row: dict, *, awarded: bool = False) -> str:
    """Список тех, кому достался месяц, с юзернеймами."""
    lines = [f'{e("calendar")} <b>Месяц подписки: '
             f'{len(row["winners"])} чел.</b>',
             f'{fmt(row["at"])} — из {row["tickets_total"]} билетов, '
             f'{row["participants"]} участников', '']

    for place, winner in enumerate(row['winners'], start=1):
        who = f' @{winner["username"]}' if winner['username'] else ' (без ника)'
        lines.append(f'{place}. <code>{ids.show(winner["user_id"])}</code>'
                     f'{who} — билет №{winner["ticket"]}')
    lines.append('')

    if awarded:
        lines.append(f'{e("ok")} Дни начислены. Объявить победителям — '
                     f'<code>/rafflenote месяц</code>.')
    else:
        lines.append(f'<blockquote>Пока ничего не начислено. Выдать по '
                     f'{MONTH_DAYS} дней этому списку — '
                     f'<code>/rafflemonth выдать</code>.\n\nСписок '
                     f'сохранён: пересдать можно командой '
                     f'<code>/rafflemonth {DEFAULT_MONTHS} заново</code>, но '
                     f'если он уже объявлен, пересдача перестаёт быть '
                     f'розыгрышем.</blockquote>')
    return '\n'.join(lines)


async def saved(c, key: str) -> dict | None:
    return await c.db[names.RAFFLE_DRAWS].find_one({'_id': key})


async def month(message: types.Message, command, c, settings) -> None:
    """`/rafflemonth` — раздать месяц подписки случайным участникам."""
    words = (command.args or '').lower().split()
    again = 'заново' in words
    give = 'выдать' in words
    count = next((int(word) for word in words if word.isdigit()), 0)

    start, end = await period(_no_args(command), settings)
    if not start or not end:
        await message.answer(NO_DATES + USAGE)
        return

    key = month_key({'start': start, 'end': end})
    row = await saved(c, key)

    if give:
        if not row:
            await message.answer(f'{e("cross")} Сначала вытащите список: '
                                 f'<code>/rafflemonth {DEFAULT_MONTHS}</code>.')
            return
        await _award_month(message, c, settings, key, row)
        return

    if row and not again:
        await message.answer(month_text(row, awarded=bool(row.get('awarded_at'))))
        return

    if not count:
        await message.answer(MONTH_USAGE)
        return

    data = await report(message, _no_args(command), c, settings)
    if data is None:
        return
    if not data['rows']:
        await message.answer(f'{e("cross")} Тащить не из чего: билетов нет.')
        return

    from app.admin.raffle import published

    snap = await published(c, data)
    tickets = (snap.get('rows') if snap else None) or data['rows']
    pool = len(tickets)

    # Победители главных призов в этом жребии не участвуют: приз в одни
    # руки — то, о чём договариваются до розыгрыша. Список берётся тот же,
    # что показывает `/rafflewinners`, — хоть брошенный ботом, хоть
    # присланный таблицей.
    main = await saved(c, key[6:])
    busy = {int(item['user_id']) for item in (main or {}).get('winners') or []}
    if busy:
        tickets = [item for item in tickets if int(item['owner']) not in busy]
    if not tickets:
        await message.answer(f'{e("cross")} Тащить не из чего: все билеты '
                             f'принадлежат победителям главных призов.')
        return

    winners = domain.draw(tickets, count)

    row = {'_id': key, 'at': now(), 'admin_id': message.from_user.id,
           'kind': 'month', 'days': MONTH_DAYS,
           'tickets_total': data['tickets'],
           'participants': len(data['participants']),
           'winners': [{'prize': MONTH_PRIZE, 'ticket': item['ticket'],
                        'user_id': item['owner'],
                        'username': item['owner_username']}
                       for item in winners]}
    await c.db[names.RAFFLE_DRAWS].delete_one({'_id': key})
    await c.db[names.RAFFLE_DRAWS].insert_one(row)
    log.info('месяцы розыгрыша %s: вытащено %s из %s участников',
             key, len(row['winners']), len(data['participants']))

    text = month_text(row)
    if busy:
        text += (f'\n{e("ok")} Победители главных призов в жребии не '
                 f'участвовали: {len(busy)} чел., их {pool - len(tickets)} '
                 f'билетов отложены.')
    if len(winners) < count:
        text += (f'\n\n{e("warning")} Просили {count}, а участников хватило '
                 f'на {len(winners)}: один человек получает не больше '
                 f'одного месяца.')
    await message.answer(text)


async def _award_month(message, c, settings, key: str, row: dict) -> None:
    """Начислить дни по сохранённому списку. Повторно — не начислит."""
    await message.answer(f'{e("refresh")} Начисляю {MONTH_DAYS} дней: '
                         f'{len(row["winners"])}…')

    mark = f'rafflemonth_{key[6:]}'[:40]
    report_data = await prizes.award(
        c.users, c.vpn,
        [{'user_id': w['user_id'], 'amount': MONTH_DAYS, 'kind': prizes.DAYS}
         for w in row['winners']],
        mark=mark, reason=f'{MONTH_PRIZE} за розыгрыш')

    await c.db[names.RAFFLE_DRAWS].update_one(
        {'_id': key}, {'$set': {'awarded_at': now(),
                                'awarded': len(report_data['done'])}})

    lines = [f'{e("ok")} <b>Месяцы начислены</b>', '',
             f'Начислено: <b>{len(report_data["done"])}</b>']
    if report_data['skipped']:
        lines.append(f'Пропущено (уже получали): '
                     f'<b>{len(report_data["skipped"])}</b>')
    if report_data['failed']:
        lines.append('')
        lines.append(f'{e("cross")} <b>Не вышло: '
                     f'{len(report_data["failed"])}</b>')
        for item in report_data['failed'][:10]:
            lines.append(f'   <code>{ids.show(item["user_id"])}</code> — '
                         f'{item["why"]}')
    lines.append('')
    lines.append(f'<blockquote>Объявить победителям — '
                 f'<code>/rafflenote месяц</code>: сначала покажет текст и '
                 f'список, отправит только по второй команде.</blockquote>')
    await message.answer('\n'.join(lines))


# ── список победителей со стороны ───────────────────────────────────────────
#
# Жребий бот умеет бросать сам (`/raffledraw`), но куда чаще числа тянут
# на видео генератором, а итог ведут в таблице: так зритель видит и
# бросок, и список. Боту этот итог всё равно нужен — иначе он не знает,
# кому писать письма. Команда принимает таблицу как есть: строку на
# человека, числа в любом порядке.

WINNERS_USAGE = (
    f'{e("trophy")} <b>Список победителей</b>\n\n'
    f'<code>/rafflewinners</code> без списка — показать всех, кто уже '
    f'записан.\n\n'
    f'Пришлите таблицу — по строке на человека. Можно прямо копией из '
    f'Excel:\n\n'
    f'<code>iPhone 18 Pro\t347****14\t5389\t347223714\n'
    f'AirPods 5\t834*****13\t1260\t8347392713</code>\n\n'
    f'Те, кому достался месяц подписки, — отдельным списком:\n'
    f'<code>/rafflewinners месяц\n802421217\n802421301</code>\n\n'
    f'<blockquote>Приз — текст в начале строки, дальше числа в любом '
    f'порядке: длинное считается идентификатором, короткое — номером '
    f'билета. Закрытые id со звёздочками пропускаются, их можно не '
    f'убирать.\n\nНичего никому не отправляется и не начисляется: список '
    f'просто запоминается, чтобы по нему работал '
    f'<code>/rafflenote</code>.</blockquote>'
)

# Короче — номер билета, длиннее — идентификатор Telegram. Границу в семь
# цифр выбрали по жизни: билетов у акции тысячи, а id начинаются с сотен
# миллионов. Совпасть они могут только если билетов станет миллион.
ID_DIGITS = 7


def _number(token: str) -> int | None:
    """«5389», «№5389», «#1» → число. Не число — None."""
    digits = (token or '').replace('№', '').replace('#', '').replace(' ', '')
    return int(digits) if digits.isdigit() else None


# Слово-подтверждение можно написать и отдельной строкой, и в начале
# первой: таблицу копируют целиком, а слово дописывают сверху.
REPLACE = ('заменить', 'replace', 'перезаписать')

# Слово «месяц» переключает команду на второй список — тех, кому достался
# месяц подписки.
MONTH_WORDS = ('месяц', 'месяца', 'месяцы', 'month')


def without_words(raw: str) -> str:
    """Убрать слова управления, оставив таблицу как есть."""
    kept = []
    control = REPLACE + MONTH_WORDS
    for line in (raw or '').splitlines():
        text = line.strip()
        if text.lower() in control:
            continue
        for word in control:
            low = text.lower()
            if low.startswith(f'{word} ') or low.startswith(f'{word}\t'):
                text = text[len(word):].strip()
                break
        if text:
            kept.append(text)
    return '\n'.join(kept)


def parse_winners(text: str) -> tuple[list[dict], list[str]]:
    """Разобрать таблицу победителей. Возвращает (строки, непонятое).

    Молча пропускать строку нельзя: это чей-то приз, и пропажу заметят
    позже всех — когда человек не получит письма.
    """
    found, broken = [], []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue

        # Закрытые id из таблицы выбрасываем сразу: они не числа и не приз.
        cells = [cell.strip().strip('«»"')
                 for cell in (line.split('\t') if '\t' in line else line.split())]
        cells = [cell for cell in cells if cell and '*' not in cell]

        # Числа читаются с конца строки, а не с начала: в названии приза
        # цифры живут на законных основаниях — «iPhone 18 Pro», «5 000 ₽».
        numbers = []
        while cells and _number(cells[-1]) is not None:
            numbers.insert(0, _number(cells.pop()))

        user_id = next((n for n in numbers if len(str(n)) >= ID_DIGITS), 0)
        shorter = [n for n in numbers if len(str(n)) < ID_DIGITS]
        if not user_id:
            broken.append(line)
            continue
        found.append({'prize': ' '.join(cells).strip(' .—-:') or 'Приз',
                      # Номер приза («#1») стоит в начале, а билет — рядом с
                      # идентификатором: берём ближайшее к нему число.
                      'ticket': shorter[-1] if shorter else 0,
                      'user_id': user_id})
    return found, broken


async def everyone(c, settings) -> str:
    """Общий список: главные призы и месяцы подписки одним экраном.

    Списка два, потому что письма у них разные, но смотреть на них
    приходится вместе: «кто вообще что выиграл» — первый вопрос, который
    задают после розыгрыша.
    """
    start, end = await period(_no_args(None), settings)
    if not start or not end:
        return NO_DATES + USAGE

    base = f'{fmt(start, "%Y%m%d")}-{fmt(end, "%Y%m%d")}'
    main = await saved(c, base)
    monthly = await saved(c, f'month-{base}')
    if not main and not monthly:
        return (f'{e("cross")} Победителей пока нет.\n\n' + WINNERS_USAGE)

    lines = [f'{e("trophy")} <b>Победители розыгрыша</b>',
             f'{fmt(start, "%d.%m.%Y")} — {fmt(end, "%d.%m.%Y")}', '']

    for title, row, extra in (
            (f'{e("gift")} Главные призы', main, ''),
            (f'{e("calendar")} Месяц подписки', monthly,
             'начислено' if (monthly or {}).get('awarded_at') else
             'дни ещё не начислены')):
        if not row or not row.get('winners'):
            continue
        head = f'<b>{title}: {len(row["winners"])}</b>'
        if extra:
            head += f' — {extra}'
        if row.get('noted_at'):
            head += f', письма отправлены {fmt(row["noted_at"], "%d.%m %H:%M")}'
        lines.append(head)
        for place, item in enumerate(row['winners'], start=1):
            who = f'@{item["username"]}' if item.get('username') else 'без ника'
            prize = (f'<b>{item["prize"]}</b> — '
                     if row is main and item.get('prize') else '')
            ticket = f', билет №{item["ticket"]}' if item.get('ticket') else ''
            lines.append(f'{place}. {prize}<code>'
                         f'{ids.show(item["user_id"])}</code> ({who}){ticket}')
        lines.append('')

    both = {int(item['user_id'])
            for row in (main, monthly) if row
            for item in row.get('winners') or []}
    lines.append(f'<blockquote>Всего людей: {len(both)}. Письма по призам — '
                 f'<code>/rafflenote</code>, по месяцам — '
                 f'<code>/rafflenote месяц</code>.</blockquote>')
    return '\n'.join(lines)


async def winners(message: types.Message, command, c, settings) -> None:
    """`/rafflewinners` со списком — записать победителей в бота."""
    raw = command.args or ''
    replace = any(word in raw.lower() for word in REPLACE)
    # Месяцы живут отдельным списком: у них свой ключ, своё письмо и своя
    # выдача. Иначе они перетёрли бы победителей призов, и наоборот.
    monthly = any(word in raw.lower().split() for word in MONTH_WORDS)
    if raw.strip().lower() in ('help', '?'):
        await message.answer(WINNERS_USAGE)
        return
    if not raw.strip():
        await message.answer(await everyone(c, settings))
        return

    rows, broken = parse_winners(without_words(raw))
    if broken:
        await message.answer(
            f'{e("cross")} Не понял строки — в них нет идентификатора:\n'
            + '\n'.join(f'<code>{line[:80]}</code>' for line in broken[:10]))
        return
    if not rows:
        await message.answer(WINNERS_USAGE)
        return

    start, end = await period(_no_args(command), settings)
    if not start or not end:
        await message.answer(NO_DATES + USAGE)
        return

    base = f'{fmt(start, "%Y%m%d")}-{fmt(end, "%Y%m%d")}'
    key = f'month-{base}' if monthly else base
    old = await saved(c, key)
    if old and not replace:
        await message.answer(
            f'{e("warning")} Список '
            + ('получателей месяца' if monthly else 'победителей')
            + f' уже записан ({len(old.get("winners") or [])} чел., '
            f'{fmt(old["at"])}).\n\n'
            f'Если он неверный — пришлите заново со словом '
            f'<code>заменить</code> в первой строке.')
        return

    # Юзернейм берётся из карточки, а не из таблицы: в отчёте о рассылке по
    # нему ищут человека руками, и устаревший ник хуже пустого.
    unknown = []
    for item in rows:
        user = await c.users.get(item['user_id'])
        item['username'] = str((user or {}).get('user_data', {}).get('username')
                               or '') if user else ''
        if not user:
            unknown.append(item)

    if monthly:
        # Приз у всех один и тот же, и писать его в каждой строке таблицы
        # незачем: в письме он всё равно не упоминается.
        for item in rows:
            item['prize'] = MONTH_PRIZE

    row = {'_id': key, 'at': now(), 'admin_id': message.from_user.id,
           'source': 'hand', 'tickets_total': 0, 'participants': len(rows),
           'winners': rows}
    if monthly:
        row['kind'], row['days'] = 'month', MONTH_DAYS
    await c.db[names.RAFFLE_DRAWS].delete_one({'_id': key})
    await c.db[names.RAFFLE_DRAWS].insert_one(row)
    log.info('победители %s записаны вручную: %s', key, len(rows))

    lines = [f'{e("trophy")} <b>Записано '
             + ('получателей месяца' if monthly else 'победителей')
             + f': {len(rows)}</b>', '']
    for place, item in enumerate(rows, start=1):
        who = f'@{item["username"]}' if item['username'] else 'без ника'
        ticket = f', билет №{item["ticket"]}' if item['ticket'] else ''
        lines.append(f'{place}. <b>{item["prize"]}</b> — '
                     f'<code>{ids.show(item["user_id"])}</code> '
                     f'({who}){ticket}')
    if unknown:
        lines.append('')
        lines.append(f'{e("warning")} <b>Нет в базе: {len(unknown)}</b> — '
                     f'письмо им не уйдёт, проверьте идентификаторы:')
        for item in unknown[:10]:
            lines.append(f'   <code>{item["user_id"]}</code>')
    lines.append('')
    if monthly:
        lines.append(f'<blockquote>Ничего не начислено. Выдать им по '
                     f'{MONTH_DAYS} дней — <code>/rafflemonth выдать</code>, '
                     f'и только после этого письма: <code>/rafflenote '
                     f'месяц</code>.</blockquote>')
    else:
        lines.append(f'<blockquote>Ничего не начислено и никому не '
                     f'отправлено. Письма — <code>/rafflenote</code>: '
                     f'сначала покажет текст и список, отправит только по '
                     f'второй команде.</blockquote>')
    await message.answer('\n'.join(lines))


# ── письма победителям ──────────────────────────────────────────────────────
def letter(winner: dict, *, month_prize: bool) -> str:
    """Текст письма одному победителю — ровно то, что он увидит."""
    key = 'raffle.month' if month_prize else 'raffle.winner'
    return texts.render(key, prize=winner.get('prize') or '',
                        ticket=winner.get('ticket') or '',
                        days=MONTH_DAYS)


def preview(row: dict, *, month_prize: bool, key: str) -> str:
    """Что уйдёт и кому — до того, как это уйдёт."""
    winners = row['winners']
    sample = letter(winners[0], month_prize=month_prize) if winners else ''

    # Письмо собирается для каждого своё, а в примере стоит первый из
    # списка — и по нему кажется, что всем уйдёт один и тот же приз.
    # Поэтому под примером сказано, чей он и что подставляется у остальных.
    first = (f'@{winners[0]["username"]}' if winners and winners[0].get('username')
             else f'№1') if winners else ''
    lines = [f'{e("envelope")} <b>Будет отправлено: {len(winners)}</b>', '',
             f'<b>Текст письма</b> — пример для {first}', '']
    lines.append(f'<blockquote>{sample}</blockquote>')
    lines.append(f'<i>У каждого подставляется свой приз и свой номер '
                 f'билета — те, что ниже.</i>')
    lines.append('')
    lines.append('<b>Кому</b>')
    for place, winner in enumerate(winners[:60], start=1):
        who = f'@{winner["username"]}' if winner['username'] else 'без ника'
        prize = f' — <b>{winner["prize"]}</b>' if winner.get('prize') else ''
        ticket = f', билет №{winner["ticket"]}' if winner.get('ticket') else ''
        lines.append(f'{place}. {who} — <code>'
                     f'{ids.show(winner["user_id"])}</code>{prize}{ticket}')
    if len(winners) > 60:
        lines.append(f'…и ещё {len(winners) - 60}')
    lines.append('')

    if row.get('noted_at'):
        lines.append(f'{e("warning")} Письма по этому списку уже уходили '
                     f'{fmt(row["noted_at"])}. Повторная отправка напишет '
                     f'тем же людям второй раз.')
        lines.append('')
    lines.append(f'<blockquote>Отправить — <code>/rafflenote'
                 f'{" месяц" if month_prize else ""} отправить</code>. '
                 f'Текст правится в /admin → Тексты.</blockquote>')
    return '\n'.join(lines)


async def note(message: types.Message, command, c, settings) -> None:
    """`/rafflenote` — показать письмо и список, по второй команде отправить."""
    words = (command.args or '').lower().split()
    month_prize = any(word in ('месяц', 'month', 'месяца') for word in words)
    send = any(word in ('отправить', 'send', 'разослать') for word in words)
    if 'help' in words or '?' in words:
        await message.answer(NOTE_USAGE)
        return

    start, end = await period(_no_args(command), settings)
    if not start or not end:
        await message.answer(NO_DATES + USAGE)
        return

    base = f'{fmt(start, "%Y%m%d")}-{fmt(end, "%Y%m%d")}'
    key = f'month-{base}' if month_prize else base
    row = await saved(c, key)
    if not row or not row.get('winners'):
        await message.answer(
            f'{e("cross")} Список пуст: сначала '
            + (f'<code>/rafflemonth {DEFAULT_MONTHS}</code>' if month_prize
               else '<code>/raffledraw</code>') + '.\n\n' + NOTE_USAGE)
        return

    # Письмо про месяц говорит «подписка продлена» — значит, к моменту
    # отправки она должна быть продлена на самом деле.
    if month_prize and not row.get('awarded_at'):
        await message.answer(
            f'{e("cross")} Месяцы ещё не начислены, а в письме сказано, что '
            f'подписка уже продлена. Сначала <code>/rafflemonth выдать</code>.')
        return

    if not send:
        await message.answer(preview(row, month_prize=month_prize, key=key))
        return

    await _send_all(message, c, key, row, month_prize=month_prize)


async def _send_all(message, c, key: str, row: dict, *,
                    month_prize: bool) -> None:
    sender = Sender(on_blocked=c.users.mark_blocked)
    await message.answer(f'{e("envelope")} Отправляю: '
                         f'{len(row["winners"])}…')

    delivered, lost = [], []
    for winner in row['winners']:
        ok = await sender.send(message.bot, int(winner['user_id']),
                               letter(winner, month_prize=month_prize))
        (delivered if ok else lost).append(winner)

    await c.db[names.RAFFLE_DRAWS].update_one(
        {'_id': key}, {'$set': {'noted_at': now(),
                                'noted': len(delivered)}})
    log.info('письма победителям %s: доставлено %s из %s',
             key, len(delivered), len(row['winners']))

    lines = [f'{e("ok")} <b>Отправлено: {len(delivered)}</b>', '']
    for winner in delivered[:60]:
        who = f'@{winner["username"]}' if winner['username'] else 'без ника'
        lines.append(f'{who} — <code>{ids.show(winner["user_id"])}</code>')
    if lost:
        lines.append('')
        lines.append(f'{e("cross")} <b>Не дошло: {len(lost)}</b> — бот '
                     f'заблокирован или чат не начат')
        for winner in lost[:30]:
            who = f'@{winner["username"]}' if winner['username'] else 'без ника'
            lines.append(f'{who} — <code>{ids.show(winner["user_id"])}</code>')
        lines.append('')
        lines.append('<i>Этим людям напишите сами: бот второй раз их не '
                     'добудится.</i>')
    await message.answer('\n'.join(lines))


def _no_args(command=None):
    """Аргументы этих команд — не даты периода, а слова управления."""
    return type('Cmd', (), {'args': ''})()


def register(router: Router) -> None:
    router.message.register(winners, Command('rafflewinners'))
    router.message.register(public, Command('rafflepublic'))
    router.message.register(month, Command('rafflemonth'))
    router.message.register(note, Command('rafflenote'))
