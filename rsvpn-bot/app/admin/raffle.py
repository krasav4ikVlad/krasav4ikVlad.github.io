"""Розыгрыш: сводка, выгрузки, жребий и выдача призов.

Команды делят одну задачу на части, каждая из которых нужна в свой день:

  * `/raffle` — как идёт: билеты, участники, деньги, что не в зачёт;
  * `/rafflestats` — статистика билетов: по дням и по людям;
  * `/raffleusers` — все участники таблицей: id, сколько билетов и откуда;
  * `/raffletickets` — все билеты таблицей, по строке на билет. Этот файл
    публикуется в канале **до** жребия;
  * `/rafflewho` — чьи это номера: числа тянет генератор на видео, бот
    только отвечает, кому билет принадлежит;
  * `/raffledraw` — жребий силами бота, если тянуть самому не хочется.
    Результат приходит только сюда, участникам бот ничего не пишет;
  * `/rafflewin` — начислить деньги и дни победителям.

Порядок «сначала публикуем билеты, потом тащим» здесь не формальность:
проверяемым розыгрыш делает именно он. Поэтому жребий сохраняется один
раз — повторный бросок, из которого выбирают понравившийся, розыгрышем уже
не является, и чтобы его повторить, нужно сказать это прямо.

Писем победителям бот не шлёт: поздравление — это разговор, и его пишет
человек, а не рассылка.
"""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command

from app.admin.money import rub
from app.content.emoji import e
from app.core import db as names
from app.core.time import fmt
from app.core.time import now as time_now
from app.domain import raffle as domain
from app.services import excel
from app.services import raffle
from app.services import raffle_prizes as prizes

log = logging.getLogger(__name__)

USAGE = (
    f'{e("gift")} <b>Розыгрыш</b>\n\n'
    f'<code>/raffle</code> — как идёт: билеты, участники, деньги\n'
    f'<code>/rafflestats</code> — статистика билетов: по дням и по людям\n'
    f'<code>/raffleusers</code> — все участники таблицей\n'
    f'<code>/raffletickets</code> — все билеты, по строке на билет\n'
    f'<code>/rafflewho 1423 77</code> — чьи это билеты\n'
    f'<code>/raffledraw</code> — жребий: кому какой приз\n'
    f'<code>/rafflebonus</code> — кому причитается подарок за порог билетов\n'
    f'<code>/raffle 01.10.2026 22.10.2026</code> — за другой период\n\n'
    f'<blockquote>Даты акции и цену билета задайте один раз в '
    f'/admin → Розыгрыш — тогда команды можно звать без аргументов.\n\n'
    f'Три билета за нового приглашённого друга, купившего подписку от '
    f'месяца, и один билет за каждый месяц своей подписки. Повторные '
    f'покупки того же друга билетов не дают, пополнение баланса — тоже: '
    f'деньги на балансе ещё не подписка.</blockquote>'
)

TICKET_COLUMNS = ('билет', 'дата', 'время', 'участник_id', 'участник_username',
                  'за_что', 'подробности', 'друг_id')

USER_COLUMNS = ('участник_id', 'username', 'билетов', 'из_них_за_друзей',
                'друзей', 'за_свою_подписку', 'первый_билет')

NO_DATES = (
    f'{e("cross")} <b>Не вижу даты акции</b>\n\n'
    f'Поэтому и кнопки «Розыгрыш» в профиле ни у кого нет: она появляется '
    f'только пока акция идёт, а «идёт» считается по этим двум датам.\n\n'
    f'Задайте их в /admin → Розыгрыш: «Начало акции» и «Конец акции», '
    f'в виде <code>22.09.2026</code>.\n\n'
)


def button_note(start, end, moment=None) -> str:
    """Видит ли человек кнопку «Розыгрыш» в профиле — и если нет, то почему.

    Кнопка показывается по датам и больше ни по чему, но снаружи этого не
    видно: пустая дата выглядит как сломанная кнопка. Один вопрос — один
    ответ, прямо в сводке.
    """
    from app.bot.handlers.raffle import AFTER_DAYS, _after

    moment = moment or time_now()
    if not start or not end:
        return (f'{e("cross")} Кнопки в профиле нет: даты акции не заданы '
                f'(/admin → Розыгрыш)')
    if moment < start:
        return (f'{e("calendar")} Кнопка появится {fmt(start, "%d.%m.%Y")} '
                f'в 00:00')
    if moment <= end + _after():
        return (f'{e("ok")} Кнопка видна всем — и ещё {AFTER_DAYS} дня после '
                f'конца акции')
    return (f'{e("cross")} Кнопка убрана: акция кончилась '
            f'{fmt(end, "%d.%m.%Y")}')


async def period(command, settings) -> tuple:
    """Период акции: из аргументов команды, иначе из настроек."""
    parts = (command.args or '').split()
    raw_start = parts[0] if parts else str(await settings.get('raffle.start') or '')
    raw_end = parts[1] if len(parts) > 1 else str(await settings.get('raffle.end') or '')

    return domain.parse_day(raw_start), domain.parse_day(raw_end, end=True)


async def report(message: types.Message, command, c, settings) -> dict | None:
    start, end = await period(command, settings)
    if not start or not end:
        await message.answer(NO_DATES + USAGE)
        return None
    if end < start:
        await message.answer(f'{e("cross")} Конец акции раньше начала.')
        return None

    # Проход по всем платежам не мгновенный, а команду зовут с телефона:
    # без этой строки кажется, что бот не ответил.
    await message.answer(f'{e("refresh")} Считаю билеты…')
    data = await raffle.collect(
        c.balance_log, c.users, start=start, end=end,
        friend_tickets=await settings.int('raffle.friend_tickets'),
        self_per_month=await settings.int('raffle.self_per_month'),
        min_months=await settings.int('raffle.min_months'),
        require_active=await settings.flag('raffle.require_active'),
        exclude=await settings.get('raffle.exclude'))
    data['journal_since'] = await raffle.journal_since(c.balance_log)
    return data


def reached(data: dict, tickets: int) -> list[dict]:
    """Кто дотянулся до порога подарка. Пустой порог — никто, а не все."""
    if tickets <= 0:
        return []
    return [item for item in data['participants'] if item['tickets'] >= tickets]


def summary(data: dict, bonus_tickets: int = 0, bonus_days: int = 0) -> str:
    lines = [f'{e("gift")} <b>Розыгрыш</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")}',
             button_note(data['start'], data['end']), '']

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

    if data.get('excluded'):
        lines.append(f'{e("ban")} Не учитываются: '
                     + ', '.join(f'<code>{item}</code>'
                                 for item in data['excluded'][:10]))
    lines.append(depth_note(data))
    lines.append('')

    lines.append(f'<blockquote>{data["friend_tickets"]} билета за нового '
                 f'друга, купившего подписку от {data["min_months"]} мес., и '
                 f'{data["self_per_month"]} билет за каждый месяц своей '
                 f'подписки. Пополнение баланса билетов не даёт: деньги на '
                 f'балансе — ещё не подписка.\n\nТаблица для розыгрыша — '
                 f'<code>/raffletickets</code>: там каждый билет отдельной '
                 f'строкой.</blockquote>')
    return '\n'.join(lines)


def depth_note(data: dict) -> str:
    """Насколько глубоко видно «покупал ли друг раньше».

    Журнал завели позже, чем запустили бота, и по нему одному старый
    покупатель выглядит новым. Вторая проверка идёт по карточкам — они
    помнят дольше, — но у карточки последние 500 записей, и у самых
    активных людей начало истории тоже обрезано. Молчать об этом нельзя:
    «точно ли учитываются только новые» — вопрос, на который отчёт обязан
    отвечать сам.
    """
    since = data.get('journal_since')
    if not since:
        return (f'{e("warning")} Журнал покупок пуст — «новизна» друга '
                f'проверяется только по карточкам.')
    return (f'{e("note")} Журнал покупок ведётся с '
            f'<b>{fmt(since, "%d.%m.%Y")}</b>; что было раньше, проверяется '
            f'по карточкам пользователей.')


def _tickets(count: int) -> str:
    """«1 билет», «2 билета», «5 билетов» — иначе в сводке видно машину."""
    if count % 10 == 1 and count % 100 != 11:
        return 'билет'
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return 'билета'
    return 'билетов'


def ticket_rows(data: dict) -> list[list]:
    """Билеты строками: один билет — одна строка."""
    return [[row['ticket'],
             fmt(row['at'], '%d.%m.%Y'),
             fmt(row['at'], '%H:%M:%S'),
             row['owner'],
             row['owner_username'],
             row['kind'],
             row['detail'],
             row['friend'] or ''] for row in data['rows']]


def user_rows(data: dict) -> list[list]:
    """Участники строками, от большего числа билетов к меньшему."""
    return [[item['user_id'],
             item['username'],
             item['tickets'],
             item['tickets'] - item['own'],
             item['friends'],
             item['own'],
             fmt(item.get('first_at'))] for item in data['participants']]


def filename(data: dict, what: str, ext: str) -> str:
    return (f'{what}-{fmt(data["start"], "%d.%m.%Y")}'
            f'-{fmt(data["end"], "%d.%m.%Y")}.{ext}')


async def send_table(message: types.Message, data: dict, *, what: str,
                     columns, rows: list[list], sheet: str, caption: str) -> None:
    body, ext = excel.build(columns, rows, sheet)
    await message.answer_document(
        types.BufferedInputFile(body, filename=filename(data, what, ext)),
        caption=caption + ('' if ext == 'xlsx' else
                           f'\n\n{e("warning")} Отдал CSV: на сервере нет '
                           f'openpyxl. Поставится сам при следующем '
                           f'обновлении бота.'))


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
                             f'нет ни одного билета.')
        return

    await send_table(
        message, data, what='raffle-tickets', columns=TICKET_COLUMNS,
        rows=ticket_rows(data), sheet='Билеты',
        caption=(
            f'{e("gift")} Билетов: <b>{data["tickets"]}</b>, участников: '
            f'<b>{len(data["participants"])}</b>\n\n'
            f'<blockquote>Одна строка — один билет, даже если друг принёс '
            f'сразу три: розыгрыш идёт по номерам, и «билет №17» должен '
            f'означать ровно одного человека.\n\n'
            f'Пронумерованы по дате покупки: номер 1 — самая первая за '
            f'акцию.\n\n'
            f'Этот файл имеет смысл выложить в канал до жребия: список, '
            f'который нельзя поменять после публикации, — единственное, что '
            f'делает случайный выбор проверяемым.</blockquote>'))


async def users(message: types.Message, command, c, settings) -> None:
    """`/raffleusers` — все участники одной таблицей.

    Отдельно от билетов: в файле билетов человек с двадцатью билетами
    занимает двадцать строк, и «сколько всего участников» по нему не
    посчитать, не сводя таблицу руками.
    """
    data = await report(message, command, c, settings)
    if data is None:
        return

    if not data['participants']:
        await message.answer(f'{e("cross")} Участников пока нет.')
        return

    await send_table(
        message, data, what='raffle-users', columns=USER_COLUMNS,
        rows=user_rows(data), sheet='Участники',
        caption=(
            f'{e("referrals")} Участников: <b>{len(data["participants"])}</b>, '
            f'билетов: <b>{data["tickets"]}</b>\n\n'
            f'<blockquote>Одна строка — один человек. Отсортированы по числу '
            f'билетов.\n\nШансы считаются по билетам, а не по строкам: у кого '
            f'билетов вдвое больше, у того и шанс вдвое выше. Тащить жребий '
            f'по этому файлу нельзя — для этого есть '
            f'<code>/raffletickets</code>.</blockquote>'))


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


# ── статистика ──────────────────────────────────────────────────────────────
#
# Сводка отвечает на «сколько всего», статистика — на «как идёт и у кого».
# Вопросы разные: тысяча билетов у пятнадцати человек и та же тысяча у
# четырёхсот — два разных результата с одинаковой суммой.

BAR = '█'
BAR_WIDTH = 12
DAYS_SHOWN = 14


def bar(value: int, top: int) -> str:
    """Полоска на строку дня. Столбик виден одним взглядом, а число —
    только если его прочитать и сравнить с соседним."""
    if top <= 0:
        return ''
    return BAR * max(1, round(value * BAR_WIDTH / top)) if value else ''


def stats_text(data: dict, numbers: dict) -> str:
    lines = [f'{e("stats")} <b>Статистика билетов</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")}', '']

    if not numbers['total']:
        lines.append('Билетов пока ни одного — считать нечего.')
        return '\n'.join(lines)

    lines.append(f'{e("cart")} Билетов: <b>{numbers["total"]}</b> '
                 f'у <b>{numbers["people"]}</b> чел.')
    lines.append(f'{e("stats")} В среднем <b>{numbers["average"]}</b>, '
                 f'у половины — <b>{numbers["median"]}</b> и меньше, '
                 f'больше всех — <b>{numbers["most"]}</b>')
    lines.append('')

    lines.append('<b>По дням</b>')
    days = numbers['days'][-DAYS_SHOWN:]
    top = max(row['tickets'] for row in days)
    for row in days:
        lines.append(f'<code>{row["day"]}  {row["tickets"]:>4}</code> '
                     f'{bar(row["tickets"], top)}'
                     + (f' <i>+{row["people"]} чел.</i>' if row['people'] else ''))
    lines.append('')

    lines.append('<b>Сколько у кого</b>')
    for row in numbers['buckets']:
        if not row['people']:
            continue
        share = round(row['people'] * 100 / numbers['people'])
        lines.append(f'<code>{row["label"]:<12}</code> {row["people"]} чел. '
                     f'({share}%)')
    lines.append('')

    lines.append('<b>Откуда билеты</b>')
    lines.append(f'За друзей: <b>{numbers["friends"]}</b> '
                 f'({round(numbers["friends"] * 100 / numbers["total"])}%) — '
                 f'привели <b>{numbers["inviters"]}</b> чел.')
    lines.append(f'За свои подписки: <b>{numbers["own"]}</b> '
                 f'({round(numbers["own"] * 100 / numbers["total"])}%)')
    lines.append('')

    lines.append(f'{e("money")} Куплено подписок на <b>{rub(data["revenue"])}</b>')
    lines.append('')
    lines.append(f'<blockquote>«+N чел.» в строке дня — столько человек '
                 f'получили свой первый билет в этот день.\n\n'
                 f'«Привели N человек» — это те, кому засчитан '
                 f'хотя бы один друг. Остальные участвуют своей подпиской: '
                 f'их всегда больше, и это нормально.\n\n'
                 f'Средним лучше не мерить: один человек с сотней билетов '
                 f'поднимает его всем. Медиана честнее — половина участников '
                 f'имеет столько билетов или меньше.\n\n'
                 f'Пофамильно — <code>/raffleusers</code>, каждый билет '
                 f'строкой — <code>/raffletickets</code>.</blockquote>')
    return '\n'.join(lines)


async def statistics(message: types.Message, command, c, settings) -> None:
    data = await report(message, command, c, settings)
    if data is None:
        return
    await message.answer(stats_text(data, domain.stats(data['rows'],
                                                       data['participants'])))


# ── кто выиграл по номеру ───────────────────────────────────────────────────
#
# Числа тянет генератор на видео, а не бот: зритель видит и опубликованный
# список билетов, и сам бросок — проверить можно всё. Боту остаётся
# ответить, чей это номер.

WHO_USAGE = (
    f'{e("gift")} <b>Кто выиграл</b>\n\n'
    f'Пришлите номера билетов — через пробел, запятую или строками:\n'
    f'<code>/rafflewho 1423 77 2890</code>\n\n'
    f'<blockquote>Порядок сохраняется: первый номер — первый приз. '
    f'Если номер достался тому, кто уже выиграл, бот скажет об этом — '
    f'тяните взамен ещё одно число, не сходя с записи.\n\n'
    f'Сам список билетов — <code>/raffletickets</code>. Выложите его в '
    f'канал до броска: список, который нельзя поменять после публикации, '
    f'и делает случайный выбор проверяемым.</blockquote>'
)


def who_text(data: dict, found: list[dict]) -> str:
    total = data['tickets']
    lines = [f'{e("gift")} <b>Кто выиграл</b>',
             f'Билетов всего: <b>{total}</b>', '']

    for item in found:
        row = item['row']
        if row is None:
            lines.append(f'<b>{item["place"]}.</b> №{item["ticket"]} — '
                         f'{e("cross")} такого билета нет (всего {total})')
            continue
        who = f' @{row["owner_username"]}' if row['owner_username'] else ''
        lines.append(f'<b>{item["place"]}.</b> №{item["ticket"]} — '
                     f'<code>{row["owner"]}</code>{who}')
        lines.append(f'      <i>{row["kind"]}, {row["detail"]}, '
                     f'{fmt(row["at"], "%d.%m %H:%M")}</i>')
        if item['repeat']:
            lines.append(f'      {e("warning")} <b>Это тот же человек, что '
                         f'и №{item["repeat"]}</b> — тяните ещё одно число')

    lines.append('')
    lines.append(f'<blockquote>Начислить деньги и дни: '
                 f'<code>/rafflewin id сумма</code> или '
                 f'<code>/rafflewin id 30д</code>, по строке на человека. '
                 f'Писем победителям бот не шлёт.</blockquote>')
    return '\n'.join(lines)


async def who(message: types.Message, command, c, settings) -> None:
    """`/rafflewho 1423 77` — чьи это билеты."""
    numbers = domain.parse_numbers(command.args or '')
    data = await report(message, _no_args(command), c, settings)
    if data is None:
        return

    if not numbers:
        await message.answer(
            f'{e("cart")} Билетов сейчас <b>{data["tickets"]}</b> — '
            f'генерируйте числа от 1 до {data["tickets"]}.\n\n' + WHO_USAGE)
        return

    await message.answer(who_text(data, domain.lookup(data['rows'], numbers)))


# ── жребий ──────────────────────────────────────────────────────────────────
#
# Результат приходит только сюда. Участникам бот ничего не пишет: объявить
# победителей — это разговор с людьми, и ведёт его человек, а не рассылка.

DRAW_NO_PRIZES = (
    f'{e("cross")} <b>Не вижу списка призов</b>\n\n'
    f'Задайте его в /admin → Розыгрыш → «Призы», по одному в строке. '
    f'Одинаковые пишутся с количеством:\n\n'
    f'<code>iPhone 18 Pro\nAirPods 5\n5000₽ x10\nПодписка на месяц x25</code>\n\n'
    f'<blockquote>Победителей будет столько же, сколько призов в списке: '
    f'каждому в отчёте пишется его приз, а не номер строки.</blockquote>'
)


def draw_id(data: dict) -> str:
    return (f'{fmt(data["start"], "%Y%m%d")}-{fmt(data["end"], "%Y%m%d")}')


def draw_text(row: dict) -> str:
    """Отчёт о жребии: кому какой приз и по какому билету."""
    lines = [f'{e("gift")} <b>Розыгрыш проведён</b>',
             f'{fmt(row["at"])} — билетов {row["tickets_total"]}, '
             f'участников {row["participants"]}', '']

    for place, winner in enumerate(row['winners'], start=1):
        who = f' @{winner["username"]}' if winner['username'] else ''
        lines.append(f'{place}. <b>{winner["prize"]}</b> — билет '
                     f'№{winner["ticket"]}, <code>{winner["user_id"]}</code>{who}')

    lines.append('')
    lines.append(f'<blockquote>Победителям бот ничего не написал — это ваш '
                 f'разговор с ними.\n\n'
                 f'Деньги и дни начисляются отдельно: <code>/rafflewin</code> '
                 f'со списком <code>id сумма</code> или <code>id 30д</code>.\n\n'
                 f'Жребий сохранён и повторно не бросается: '
                 f'<code>/raffledraw</code> без аргументов покажет этот же '
                 f'результат. Пересдать — <code>/raffledraw заново</code>, '
                 f'но если список билетов уже опубликован, пересдача перестаёт '
                 f'быть розыгрышем.</blockquote>')
    return '\n'.join(lines)


async def saved_draw(c, key: str) -> dict | None:
    return await c.db[names.RAFFLE_DRAWS].find_one({'_id': key})


async def drawing(message: types.Message, command, c, settings) -> None:
    """`/raffledraw` — вытащить победителей. Только в этот чат."""
    again = 'заново' in (command.args or '').lower()

    start, end = await period(_no_args(command), settings)
    if not start or not end:
        await message.answer(NO_DATES + USAGE)
        return

    key = draw_id({'start': start, 'end': end})
    old = await saved_draw(c, key)
    if old and not again:
        await message.answer(draw_text(old))
        return

    prize_list = domain.parse_prizes(str(await settings.get('raffle.prizes') or ''))
    if not prize_list:
        await message.answer(DRAW_NO_PRIZES)
        return

    data = await report(message, _no_args(command), c, settings)
    if data is None:
        return
    if not data['rows']:
        await message.answer(f'{e("cross")} Тащить не из чего: билетов нет.')
        return

    winners = domain.draw(data['rows'], len(prize_list))
    row = {
        '_id': key, 'at': time_now(), 'admin_id': message.from_user.id,
        'tickets_total': data['tickets'],
        'participants': len(data['participants']),
        'winners': [{'prize': prize, 'ticket': winner['ticket'],
                     'user_id': winner['owner'],
                     'username': winner['owner_username']}
                    for prize, winner in zip(prize_list, winners)],
    }
    if len(winners) < len(prize_list):
        row['short'] = len(prize_list) - len(winners)

    # Пересдача заменяет прошлый бросок целиком: два жребия на один период
    # — это уже выбор из двух результатов, а не розыгрыш.
    await c.db[names.RAFFLE_DRAWS].delete_one({'_id': key})
    await c.db[names.RAFFLE_DRAWS].insert_one(row)
    log.info('жребий %s: победителей %s из %s участников',
             key, len(row['winners']), len(data['participants']))

    text = draw_text(row)
    if row.get('short'):
        text += (f'\n\n{e("warning")} Призов больше, чем участников: '
                 f'{row["short"]} осталось без хозяина. Один человек берёт '
                 f'не больше одного приза.')
    await message.answer(text)


def _no_args(command):
    """Аргументы `/raffledraw` — про пересдачу, а не про даты периода."""
    return type('Cmd', (), {'args': ''})()


WIN_USAGE = (
    f'{e("gift")} <b>Выдача призов</b>\n\n'
    f'Пришлите список победителей — по строке на человека:\n'
    f'<code>802421217 5000</code> — деньги на баланс\n'
    f'<code>802421217 30д</code> — дни подписки\n\n'
    f'<blockquote>Можно одним сообщением: скопируйте столбец из таблицы, '
    f'где выбирали победителей. Повторная выдача тому же человеку в рамках '
    f'одного розыгрыша не пройдёт — отметка ставится в его карточке, и '
    f'второе нажатие «на всякий случай» призы не удвоит.\n\n'
    f'Победителям бот ничего не пишет — только начисляет. '
    f'Поздравить их вы напишете сами.</blockquote>'
)


async def win(message: types.Message, command, c, settings) -> None:
    """`/rafflewin` со списком победителей — начислить деньги и дни.

    Писем не шлёт: поздравление — это разговор, и его пишет человек. Бот
    здесь отвечает только за то, чтобы деньги и дни дошли.
    """
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

    lines = [f'{e("ok")} <b>Призы начислены</b>', '',
             f'Начислено: <b>{len(report_data["done"])}</b>']
    for row in report_data['done'][:40]:
        what = (f'{row["amount"]}₽' if row['kind'] == 'money'
                else f'+{row["amount"]} дн.')
        lines.append(f'   <code>{row["user_id"]}</code> — {what}')
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
    router.message.register(statistics, Command('rafflestats'))
    router.message.register(who, Command('rafflewho'))
    router.message.register(drawing, Command('raffledraw'))
    router.message.register(users, Command('raffleusers'))
    router.message.register(command, Command('raffle'))
    router.message.register(tickets, Command('raffletickets'))
    router.message.register(bonus, Command('rafflebonus'))
