"""/history — что человек делал, прямо в Telegram.

    /history 802421217
    /history @krasav4ik_v

История лежит в документе пользователя (последние 350 действий), но без
этой команды прочитать её можно только через Compass — то есть оператору
она недоступна. А именно оператору она и нужна: «я нажал продлить, и
списалось дважды» проверяется за десять секунд, если видно и нажатия, и
движения денег.

Деньги идут из info.transactions, а не из истории: там они уже есть, и
дублировать их вторым списком значило бы получить два источника правды
с разными числами.
"""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from app.content.emoji import e
from app.core.time import fmt, parse_dt

PAGE = 25


def _line(item: dict) -> str:
    when = fmt(parse_dt(item.get('dt')))
    action = item.get('action', '')
    details = item.get('details', '')
    return f'<code>{when}</code>  {action}' + (f' — {details}' if details else '')


def _money(item) -> str:
    """Транзакция в человеческий вид. Формат исторически трёх видов:
    список из старого бота, словарь нового и промо-запись — поддержаны все,
    иначе половина истории превратилась бы в пустые строки."""
    if isinstance(item, list):
        amount = item[0] if item else 0
        when = item[1] if len(item) > 1 else None
        title = item[2] if len(item) > 2 else ''
    elif isinstance(item, dict):
        amount = item.get('amount', 0)
        when = item.get('dt') or item.get('created_at')
        title = item.get('description') or item.get('comment') or item.get('type', '')
    else:
        return ''

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        amount = 0
    sign = '+' if amount >= 0 else '−'
    return f'<code>{fmt(parse_dt(when))}</code>  {sign}{abs(amount)}₽  {title}'


async def history(message: types.Message, command: CommandObject, c) -> None:
    args = (command.args or '').strip()
    if not args:
        await message.answer(
            f'{e("note")} <b>История пользователя</b>\n\n'
            'Кого смотрим? <code>/history 802421217</code> или '
            '<code>/history @username</code>')
        return

    user = await c.moderation.find_user(args)
    if not user:
        await message.answer(f'Пользователь <code>{args}</code> не найден.')
        return

    data = user.get('user_data') or {}
    info = user.get('info') or {}
    name = f'@{data["username"]}' if data.get('username') else 'без юзернейма'

    actions = (user.get('logs') or [])[-PAGE:]
    transactions = (info.get('transactions') or [])[-10:]

    lines = [f'{e("note")} <b>История</b> {name} '
             f'(<code>{data.get("user_id", "?")}</code>)',
             f'<b>Баланс:</b> <code>{info.get("balance", 0)}₽</code>', '']

    lines.append(f'<b>{e("money")} Последние операции</b>')
    lines += [_money(item) for item in transactions] or ['—']

    lines.append(f'\n<b>{e("clipboard")} Последние действия</b>')
    if actions:
        lines += [_line(item) for item in reversed(actions)]
    else:
        lines.append('— пусто. Журнал пишется, только пока включена настройка '
                     '«Писать действия в карточку пользователя»')

    text = '\n'.join(lines)
    # У Telegram предел 4096 символов, а 25 действий с датами в него не
    # всегда влезают: режем по границе строки, а не посередине тега.
    while len(text) > 4000:
        lines.pop()
        text = '\n'.join(lines) + '\n…'
    await message.answer(text)


def register(router: Router) -> None:
    router.message.register(history, Command('history'))
