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
from app.services import raffle_prizes as prizes

USAGE = (
    f'{e("gift")} <b>Розыгрыш</b>\n\n'
    f'<code>/raffle</code> — как идёт: билеты, участники, деньги\n'
    f'<code>/raffletickets</code> — таблица билетов файлом (CSV)\n'
    f'<code>/rafflebonus</code> — кому причитается подарок за порог билетов\n'
    f'<code>/raffle 01.10.2026 22.10.2026</code> — за другой период\n\n'
    f'<blockquote>Даты акции и цену билета задайте один раз в '
    f'/admin → Розыгрыш — тогда команды можно звать без аргументов.\n\n'
    f'Три билета за нового приглашённого друга, купившего подписку от '
    f'месяца, и один билет за каждый месяц своей подписки. Повторные '
    f'покупки того же друга билетов не дают, пополнение баланса — тоже: '
    f'деньги на балансе ещё не подписка.</blockquote>'
)

COLUMNS = ('билет', 'дата', 'время', 'участник_id', 'участник_username',
           'за_что', 'подробности', 'друг_id')


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
        c.balance_log, c.users, start=start, end=end,
        friend_tickets=await settings.int('raffle.friend_tickets'),
        self_per_month=await settings.int('raffle.self_per_month'),
        min_months=await settings.int('raffle.min_months'),
        require_active=await settings.flag('raffle.require_active'))


def reached(data: dict, tickets: int) -> list[dict]:
    """Кто дотянулся до порога подарка. Пустой порог — никто, а не все."""
    if tickets <= 0:
        return []
    return [item for item in data['participants'] if item['tickets'] >= tickets]


def summary(data: dict, bonus_tickets: int = 0, bonus_days: int = 0) -> str:
    lines = [f'{e("gift")} <b>Розыгрыш</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")}', '']

    if not data['tickets']:
        lines.append('Билетов пока ни одного.')
        lines.append('')
        lines.append('<i>Это не обязательно поломка: билет даётся за '
                     'купленную подписку — свою или нового друга. Если акция '
                     'только началась, так и должно быть.</i>')
        return '\n'.join(lines)

    by_friends = sum(row['tickets'] for row in data['events']
                     if row['valid'] and row['kind'] == domain.FRIEND)
    lines.append(f'{e("cart")} Билетов: <b>{data["tickets"]}</b> — '
                 f'за друзей {by_friends}, за свои подписки '
                 f'{data["tickets"] - by_friends}')
    lines.append(f'{e("referrals")} Участников: <b>{len(data["participants"])}</b>')
    lines.append(f'{e("money")} Куплено подписок на: <b>{data["revenue"]}₽</b>')

    # Подарок за порог — единственный расход акции, который не разыгрывается,
    # а причитается. Его цену нужно видеть до розыгрыша, а не после.
    if bonus_tickets and bonus_days:
        winners = reached(data, bonus_tickets)
        lines.append(f'{e("calendar")} Порог {bonus_tickets} '
                     f'{_tickets(bonus_tickets)} прошли: '
                     f'<b>{len(winners)}</b> — им +{bonus_days} дн.')
    lines.append('')

    lines.append('<b>Больше всех привели</b>')
    for place, item in enumerate(data['participants'][:10], start=1):
        who = f'@{item["username"]}' if item['username'] else 'без юзернейма'
        lines.append(f'{place}. <code>{item["user_id"]}</code> ({who}) — '
                     f'<b>{item["tickets"]}</b> {_tickets(item["tickets"])} '
                     f'(друзей {item["friends"]}, своих {item["own"]})')
    lines.append('')

    if data['skipped']:
        lines.append('<b>Не в зачёт</b>')
        for why, count in sorted(data['skipped'].items(),
                                 key=lambda pair: -pair[1]):
            lines.append(f'{why} — {count}')
        lines.append('')

    lines.append(f'<blockquote>{data["friend_tickets"]} билета за нового '
                 f'друга, купившего подписку от {data["min_months"]} мес., и '
                 f'{data["self_per_month"]} билет за каждый месяц своей '
                 f'подписки. Пополнение баланса билетов не даёт: деньги на '
                 f'балансе — ещё не подписка.\n\nТаблица для розыгрыша — '
                 f'<code>/raffletickets</code>: там каждый билет отдельной '
                 f'строкой.</blockquote>')
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
            row['ticket'],
            fmt(row['at'], '%d.%m.%Y'),
            fmt(row['at'], '%H:%M:%S'),
            row['owner'],
            row['owner_username'],
            row['kind'],
            row['detail'],
            row['friend'] or '',
        ])
    return buffer.getvalue().encode('utf-8-sig')


def filename(data: dict) -> str:
    return (f'raffle-{fmt(data["start"], "%d.%m.%Y")}'
            f'-{fmt(data["end"], "%d.%m.%Y")}.csv')


async def command(message: types.Message, command, c, settings) -> None:
    data = await report(message, command, c, settings)
    if data is not None:
        await message.answer(summary(
            data,
            bonus_tickets=await settings.int('raffle.bonus_tickets'),
            bonus_days=await settings.int('raffle.bonus_days')))


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
            f'<b>{len(data["participants"])}</b>\n\n'
            f'<blockquote>Одна строка — один билет, даже если друг принёс '
            f'сразу три: розыгрыш идёт по номерам, и «билет №17» должен '
            f'означать ровно одного человека.\n\n'
            f'Пронумерованы по дате покупки: номер 1 — самая первая за '
            f'акцию.\n\n'
            f'Этот файл имеет смысл выложить в канал до розыгрыша: список, '
            f'который нельзя поменять после публикации, — единственное, что '
            f'делает случайный выбор проверяемым.</blockquote>'))


async def bonus(message: types.Message, command, c, settings) -> None:
    """`/rafflebonus` — кому причитается подарок за порог билетов.

    Отдельной командой, а не строкой в сводке: это список для начисления, и
    из него копируют id. В сводке он занял бы весь экран.
    """
    need = await settings.int('raffle.bonus_tickets')
    days = await settings.int('raffle.bonus_days')
    if need <= 0 or days <= 0:
        await message.answer(
            f'{e("cross")} Подарок за порог выключен. Включается в '
            f'/admin → Розыгрыш: «Подарок за сколько билетов» и «Сколько '
            f'дней дарим».')
        return

    data = await report(message, command, c, settings)
    if data is None:
        return

    winners = reached(data, need)
    if not winners:
        await message.answer(
            f'{e("gift")} Порог в {need} {_tickets(need)} пока никто не прошёл.')
        return

    lines = [f'{e("gift")} <b>Подарок за {need} {_tickets(need)}: '
             f'+{days} дн.</b>',
             f'Человек: <b>{len(winners)}</b>', '']
    for item in winners:
        who = f' @{item["username"]}' if item['username'] else ''
        lines.append(f'<code>{item["user_id"]}</code>{who} — '
                     f'{item["tickets"]} {_tickets(item["tickets"])}')

    lines.append('')
    lines.append('<blockquote>Это не розыгрыш, а то, что причитается: '
                 'начислять всем из списка. Порог и число дней меняются в '
                 '/admin → Розыгрыш.</blockquote>')
    await message.answer('\n'.join(lines))


WIN_USAGE = (
    f'{e("gift")} <b>Выдача призов</b>\n\n'
    f'Пришлите список победителей — по строке на человека:\n'
    f'<code>802421217 5000</code> — деньги на баланс\n'
    f'<code>802421217 30д</code> — дни подписки\n\n'
    f'<blockquote>Можно одним сообщением: скопируйте столбец из таблицы, '
    f'где выбирали победителей. Повторная выдача тому же человеку в рамках '
    f'одного розыгрыша не пройдёт — отметка ставится в его карточке, и '
    f'второе нажатие «на всякий случай» призы не удвоит.\n\n'
    f'Каждому уйдёт письмо. Текст письма — в /admin → Розыгрыш.</blockquote>'
)


async def win(message: types.Message, command, c, settings) -> None:
    """`/rafflewin` со списком победителей — начислить призы и написать им."""
    raw = (command.args or '')
    if not raw.strip():
        await message.answer(WIN_USAGE)
        return

    winners, broken = prizes.parse_list(raw)
    if broken:
        await message.answer(
            f'{e("cross")} Не понял строки — проверьте и пришлите заново:\n'
            + '\n'.join(f'<code>{line}</code>' for line in broken[:10]))
        return
    if not winners:
        await message.answer(WIN_USAGE)
        return

    await message.answer(f'{e("refresh")} Выдаю призы: {len(winners)}…')
    mark = f'raffle_{domain.parse_day(str(await settings.get("raffle.end") or "")) or ""}'[:40]
    report_data = await prizes.award(c.users, c.vpn, winners, mark=mark)

    # Письма — через Sender: он знает про флуд-лимит и про тех, кто закрыл
    # бота. Прямая отправка сорока подряд упирается в лимит.
    from app.campaigns.sender import Sender

    sender = Sender(on_blocked=c.users.mark_blocked)
    text = str(await settings.get('raffle.win_text') or '').strip()
    lost = 0
    for winner in report_data['done']:
        if not await sender.send(message.bot, winner['user_id'],
                                 prizes.letter(winner, text)):
            lost += 1

    lines = [f'{e("ok")} <b>Призы выданы</b>', '',
             f'Начислено: <b>{len(report_data["done"])}</b>']
    if lost:
        lines.append(f'{e("attention")} Не доставлено писем: <b>{lost}</b> — '
                     f'эти люди закрыли бота. Приз начислен всё равно.')
    if report_data['skipped']:
        lines.append(f'Пропущено (уже получали): '
                     f'<b>{len(report_data["skipped"])}</b>')
    if report_data['failed']:
        lines.append('')
        lines.append(f'{e("cross")} <b>Не вышло: {len(report_data["failed"])}</b>')
        for row in report_data['failed'][:10]:
            lines.append(f'   <code>{row["user_id"]}</code> — {row["why"]}')
    await message.answer('\n'.join(lines))


def register(router: Router) -> None:
    router.message.register(win, Command('rafflewin'))
    router.message.register(command, Command('raffle'))
    router.message.register(tickets, Command('raffletickets'))
    router.message.register(bonus, Command('rafflebonus'))
