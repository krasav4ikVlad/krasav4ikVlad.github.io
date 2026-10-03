"""Итоги розыгрыша: публичный файл билетов, месяцы подписки и письма.

Три команды, которые нужны уже после жребия, когда призы розданы и пора
объявлять:

  * `/rafflepublic` — файл билетов для публикации: номер, дата, время и
    закрытый id. Полных id в нём нет вовсе, поэтому его можно выложить в
    канал, а не рассказывать словами, что список «где-то есть»;
  * `/rafflemonth` — раздать месяц подписки случайным участникам. Тянется
    так же, как призы: шанс пропорционален билетам, один человек получает
    не больше одного месяца;
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

    lines = [f'{e("envelope")} <b>Будет отправлено: {len(winners)}</b>', '',
             '<b>Текст письма</b>', '']
    lines.append(f'<blockquote>{sample}</blockquote>')
    lines.append('')
    lines.append('<b>Кому</b>')
    for place, winner in enumerate(winners[:60], start=1):
        who = f'@{winner["username"]}' if winner['username'] else 'без ника'
        lines.append(f'{place}. {who} — <code>'
                     f'{ids.show(winner["user_id"])}</code>')
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


def _no_args(command):
    """Аргументы этих команд — не даты периода, а слова управления."""
    return type('Cmd', (), {'args': ''})()


def register(router: Router) -> None:
    router.message.register(public, Command('rafflepublic'))
    router.message.register(month, Command('rafflemonth'))
    router.message.register(note, Command('rafflenote'))
