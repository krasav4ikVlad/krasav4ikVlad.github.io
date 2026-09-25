"""Кто приходил, пока бот молчал, — и письмо этим людям.

Отвечает на вопрос, который возникает после каждого простоя: «кому теперь
извиняться». Список собирается по журналу действий, а письмо уходит через
обычную рассылку — с предпросмотром, паузами, продолжением после обрыва.
Ничего своего для отправки здесь нет нарочно: вторая реализация рассылки
означала бы второй набор тех же граблей.

Вместе с людьми команда показывает оплаты за то же время. Если с ботом
лежал и API, деньги могли уйти провайдеру и не дойти до баланса — и это
важнее неотвеченного нажатия.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content import ids
from app.content.emoji import e
from app.core.time import MSK, fmt, now
from app.services import outage

DEFAULT_HOURS = 12

USAGE = (
    f'{e("warning")} <b>Кто приходил, пока бот молчал</b>\n\n'
    f'<code>/outage</code> — за последние {DEFAULT_HOURS} часов\n'
    f'<code>/outage 8</code> — за последние 8 часов\n'
    f'<code>/outage 17.09.2026 02:00 09:00</code> — точное окно\n'
    f'<code>/outage 16.09.2026 23:00 17.09.2026 09:00</code> — через полночь\n\n'
    f'<blockquote>Telegram хранит недоставленное около суток и отдаёт всё '
    f'сразу после подъёма, а бот запускается, не выбрасывая очередь. '
    f'Поэтому те, кто писал ночью, попадают в журнал <b>временем '
    f'перезапуска</b>, а не временем нажатия: если бот лежал и поднялся в '
    f'9:00, берите окно вокруг 9:00, а не ночное.</blockquote>'
)

SUGGESTED = (
    'Привет! Этой ночью бот не отвечал — вы могли писать и не получить '
    'ответа. Сам VPN работал как обычно, подписки это не коснулось: '
    'молчал только бот.\n\n'
    'Уже всё починили. Если у вас остался вопрос или что-то не получилось '
    'сделать — напишите ещё раз, всё работает.\n\n'
    'Извините за потраченное время.'
)


def parse_window(args: str, moment: datetime | None = None) -> tuple:
    """Окно из аргументов команды. Ошиблись — вернём (None, None), а не гадаем."""
    moment = moment or now()
    parts = (args or '').split()

    if not parts:
        return moment - timedelta(hours=DEFAULT_HOURS), moment

    if len(parts) == 1:
        if not parts[0].isdigit() or int(parts[0]) <= 0:
            return None, None
        return moment - timedelta(hours=int(parts[0])), moment

    if len(parts) == 3:
        start = _moment(parts[0], parts[1])
        end = _moment(parts[0], parts[2])
        # «с 23:00 до 09:00» — это через полночь, а не окно в минус десять
        # часов: то, как о ночи говорят люди.
        if start and end and end <= start:
            end += timedelta(days=1)
        return start, end

    if len(parts) == 4:
        return _moment(parts[0], parts[1]), _moment(parts[2], parts[3])

    return None, None


def _moment(day: str, clock: str) -> datetime | None:
    for pattern in ('%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M', '%d.%m.%y %H:%M'):
        try:
            return datetime.strptime(f'{day} {clock}', pattern).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


def summary(data: dict) -> str:
    start, end = data['start'], data['end']
    lines = [f'{e("warning")} <b>Кто приходил, пока бот молчал</b>',
             f'{fmt(start)} — {fmt(end, "%H:%M")}', '']

    if not data['total']:
        lines.append('В этом окне обращений в журнале нет.')
        lines.append('')
        lines.append('<i>Если бот в это время лежал, так и должно быть: '
                     'записывать было некому. Обращения появятся временем '
                     'перезапуска — возьмите окно вокруг него.</i>')
    else:
        hits = sum(item['hits'] for item in data['people'])
        active = len([item for item in data['people'] if item['active']])
        lines.append(f'{e("referrals")} Человек: <b>{data["total"]}</b>, '
                     f'обращений: <b>{hits}</b>')
        lines.append(f'   с активной подпиской: <b>{active}</b>')
        if data['blocked']:
            lines.append(f'   заблокировали бота: <b>{data["blocked"]}</b> — '
                         f'им письмо не дойдёт')
        if data['stopped']:
            lines.append(f'   {e("attention")} список обрезан по пределу')
        lines.append('')

    money = data['payments']
    if money['total']:
        lines.append(f'{e("money")} Оплат за это время: <b>{money["total"]}</b>, '
                     f'зачислено: <b>{money["done"]}</b>')
        if money['stuck']:
            lines.append(f'{e("attention")} <b>Не зачислено: '
                         f'{len(money["stuck"])} на {money["lost"]}₽</b> — '
                         f'деньги ушли провайдеру, а на баланс не легли:')
            for row in money['stuck'][:10]:
                lines.append(f'   <code>{ids.show(row["user_id"])}</code> — '
                             f'{row["amount"]}₽, {row["provider"]}, '
                             f'{row["status"]}, {fmt(row["at"], "%H:%M")}')
            lines.append('   <i>Проверьте их в кабинете провайдера: если '
                         'платёж там прошёл, зачислите руками.</i>')
        lines.append('')

    if data['total']:
        lines.append('<b>Стучались чаще всех</b>')
        for item in data['people'][:10]:
            who = f'@{item["username"]}' if item['username'] else 'без юзернейма'
            lines.append(f'<code>{ids.show(item["user_id"])}</code> ({who}) — '
                         f'{item["hits"]}, '
                         f'{fmt(item["first"], "%H:%M")} → '
                         f'{fmt(item["last"], "%H:%M")}')

    return '\n'.join(lines)


def keyboard(data: dict) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if data['total']:
        kb.row(types.InlineKeyboardButton(
            text=f'{e("broadcast")} Написать этим людям ({data["total"]})',
            callback_data=Adm(act='outg',
                              a=str(int(data['start'].timestamp())),
                              b=str(int(data['end'].timestamp()))).pack()))
    return kb


async def command(message: types.Message, command, c, settings) -> None:
    start, end = parse_window(command.args or '')
    if not start or not end:
        await message.answer(f'{e("cross")} Не понял окно.\n\n{USAGE}')
        return
    if end <= start:
        await message.answer(f'{e("cross")} Конец окна раньше начала.')
        return

    await message.answer(f'{e("refresh")} Смотрю журнал…')
    data = await outage.report(c.users, c.payments_repo, start, end)
    await message.answer(summary(data), reply_markup=keyboard(data).as_markup())


async def write(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                c, settings) -> None:
    """Собрать получателей и отдать их обычной рассылке."""
    from app.admin.broadcast import Broadcast

    start = datetime.fromtimestamp(int(callback_data.a), MSK)
    end = datetime.fromtimestamp(int(callback_data.b), MSK)

    await call.answer('Собираю получателей…')
    found = await outage.visitors(c.users, start, end)
    recipients = [item['user_id'] for item in found['people'] if item['user_id']]
    if not recipients:
        await call.answer('Писать некому', show_alert=True)
        return

    await state.set_state(Broadcast.text)
    await state.update_data(audience='outage', one=False,
                            total=len(recipients), recipients=recipients)

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Отмена', callback_data=Adm(act='main').pack()))
    await call.message.answer(
        f'{e("broadcast")} <b>Письмо тем, кто приходил</b>\n\n'
        f'Получателей: <code>{len(recipients)}</code>\n'
        f'Окно: {fmt(start)} — {fmt(end, "%H:%M")}\n\n'
        f'Отправьте текст сообщения — дальше будет предпросмотр и '
        f'подтверждение. Работает HTML-разметка.', reply_markup=kb.as_markup())

    # Готовый текст — отдельным сообщением и без разметки: его пересылают
    # себе и правят, а из сообщения с тегами пришлось бы вычищать их руками.
    await call.message.answer('Готовый текст, если не хотите писать свой:')
    await call.message.answer(SUGGESTED)


def register(router: Router) -> None:
    router.message.register(command, Command('outage'))
    router.callback_query.register(write, Adm.filter(F.act == 'outg'))
