"""Розыгрыш: сводка и таблица билетов.

Две команды на одну задачу. `/raffle` отвечает на вопрос «как идёт»:
билетов, участников, сколько денег принесло и что в зачёт не пошло.
`/raffletickets` отдаёт CSV — тот самый список, по которому выбирают
победителей и который публикуется в канале до розыгрыша.

Сам розыгрыш бот не проводит нарочно. Список билетов, лежащий у всех на
руках, и жребий на глазах — это проверяемо; кнопка «выбрать победителя»
внутри бота проверяемой быть не может, что бы мы о ней ни написали.
"""

from __future__ import annotations

import csv
import io

from aiogram import Router, types
from aiogram.filters import Command

from app.content.emoji import e
from app.core.time import fmt
from app.domain import raffle as domain
from app.services import raffle

USAGE = (
    f'{e("gift")} <b>Розыгрыш</b>\n\n'
    f'<code>/raffle</code> — как идёт: билеты, участники, деньги\n'
    f'<code>/raffletickets</code> — таблица билетов файлом (CSV)\n'
    f'<code>/raffle 01.10.2026 22.10.2026</code> — за другой период\n\n'
    f'<blockquote>Даты акции и минимальную оплату друга задайте один раз в '
    f'/admin → Розыгрыш — тогда команды можно звать без аргументов.\n\n'
    f'Билет даётся за друга, который пришёл по ссылке участника и '
    f'<b>впервые</b> заплатил настоящими деньгами в период акции. '
    f'Продления старых друзей билетов не дают, иначе их набрал бы тот, кто '
    f'ничего для акции не делал.</blockquote>'
)

COLUMNS = ('билет', 'дата', 'время', 'участник_id', 'участник_username',
           'друг_id', 'друг_username', 'оплатил', 'подозрение', 'в_зачёт',
           'почему_нет')


async def period(command, settings) -> tuple:
    """Период акции: из аргументов команды, иначе из настроек."""
    parts = (command.args or '').split()
    raw_start = parts[0] if parts else str(await settings.get('raffle.start') or '')
    raw_end = parts[1] if len(parts) > 1 else str(await settings.get('raffle.end') or '')

    return domain.parse_day(raw_start), domain.parse_day(raw_end, end=True)


async def report(message: types.Message, command, c, settings) -> dict | None:
    start, end = await period(command, settings)
    if not start or not end:
        await message.answer(
            f'{e("cross")} Не вижу даты акции.\n\n' + USAGE)
        return None
    if end < start:
        await message.answer(f'{e("cross")} Конец акции раньше начала.')
        return None

    # Проход по всем платежам не мгновенный, а команду зовут с телефона:
    # без этой строки кажется, что бот не ответил.
    await message.answer(f'{e("refresh")} Считаю билеты…')
    return await raffle.collect(
        c.payments_repo, c.users, start=start, end=end,
        min_payment=await settings.int('raffle.min_payment'),
        require_active=await settings.flag('raffle.require_active'))


def summary(data: dict) -> str:
    lines = [f'{e("gift")} <b>Розыгрыш</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")}', '']

    if not data['tickets']:
        lines.append('Билетов пока ни одного.')
        lines.append('')
        lines.append('<i>Это не обязательно поломка: билет даётся за друга, '
                     'который заплатил <b>впервые</b> в период акции. Если '
                     'акция только началась, так и должно быть.</i>')
        return '\n'.join(lines)

    lines.append(f'{e("cart")} Билетов: <b>{data["tickets"]}</b>')
    lines.append(f'{e("referrals")} Участников: <b>{len(data["participants"])}</b>')
    lines.append(f'{e("money")} Принесли денег: <b>{data["revenue"]}₽</b>')
    if data['flagged']:
        lines.append(f'{e("warning")} С пометкой подозрения: '
                     f'<b>{data["flagged"]}</b>')
    lines.append('')

    lines.append('<b>Больше всех привели</b>')
    for place, item in enumerate(data['participants'][:10], start=1):
        who = f'@{item["username"]}' if item['username'] else 'без юзернейма'
        lines.append(f'{place}. <code>{item["user_id"]}</code> ({who}) — '
                     f'<b>{item["tickets"]}</b> {_tickets(item["tickets"])}')
    lines.append('')

    if data['skipped']:
        lines.append('<b>Не в зачёт</b>')
        for why, count in sorted(data['skipped'].items(),
                                 key=lambda pair: -pair[1]):
            lines.append(f'{why} — {count}')
        lines.append('')

    lines.append(f'<blockquote>Считались оплаты от '
                 f'{data["min_payment"]}₽; подписка друга на сейчас '
                 + ('обязана быть активной' if data['require_active']
                    else 'может быть любой') + '. '
                 f'Таблица для розыгрыша — <code>/raffletickets</code>: там '
                 f'каждый билет с датой, и отказы с причиной.</blockquote>')
    return '\n'.join(lines)


def _tickets(count: int) -> str:
    """«1 билет», «2 билета», «5 билетов» — иначе в сводке видно машину."""
    if count % 10 == 1 and count % 100 != 11:
        return 'билет'
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return 'билета'
    return 'билетов'


def to_csv(data: dict) -> bytes:
    """CSV с BOM и точкой с запятой: так его открывает Excel без танцев.

    Без BOM русские заголовки в Excel превращаются в кракозябры, а без
    точки с запятой вся строка попадает в одну ячейку — и то и другое
    человек чинит руками на каждой выгрузке.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(COLUMNS)

    for row in data['rows']:
        writer.writerow([
            row['ticket'] or '',
            fmt(row['at'], '%d.%m.%Y'),
            fmt(row['at'], '%H:%M:%S'),
            row['referrer_id'] or '',
            row['referrer_username'] or '',
            row['friend_id'],
            row['friend_username'] or '',
            row['amount'],
            ', '.join(row['flags']),
            'да' if row['valid'] else 'нет',
            row['why'],
        ])
    return buffer.getvalue().encode('utf-8-sig')


def filename(data: dict) -> str:
    return (f'raffle-{fmt(data["start"], "%d.%m.%Y")}'
            f'-{fmt(data["end"], "%d.%m.%Y")}.csv')


async def command(message: types.Message, command, c, settings) -> None:
    data = await report(message, command, c, settings)
    if data is not None:
        await message.answer(summary(data))


async def tickets(message: types.Message, command, c, settings) -> None:
    data = await report(message, command, c, settings)
    if data is None:
        return

    if not data['rows']:
        await message.answer(f'{e("cross")} Выгружать нечего: за этот период '
                             f'нет ни одной оплаты новых плательщиков.')
        return

    await message.answer_document(
        types.BufferedInputFile(to_csv(data), filename=filename(data)),
        caption=(
            f'{e("gift")} Билетов: <b>{data["tickets"]}</b>, участников: '
            f'<b>{len(data["participants"])}</b>\n'
            f'Строк в файле: {len(data["rows"])} — вместе с теми, что в зачёт '
            f'не пошли (столбец «в_зачёт»).\n\n'
            f'<blockquote>Билеты пронумерованы по дате оплаты: номер 1 — '
            f'самый первый привод за акцию. Столбец «подозрение» — не '
            f'приговор, а повод посмотреть глазами: там пачки оплат за час, '
            f'один кошелёк на разных аккаунтах и те, кто заплатил, но так и '
            f'не подключился.\n\n'
            f'Этот файл имеет смысл выложить в канал до розыгрыша: список, '
            f'который нельзя поменять после публикации, — единственное, что '
            f'делает случайный выбор проверяемым.</blockquote>'))


def register(router: Router) -> None:
    router.message.register(command, Command('raffle'))
    router.message.register(tickets, Command('raffletickets'))
