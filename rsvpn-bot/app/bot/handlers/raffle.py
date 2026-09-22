"""Экран розыгрыша: мои билеты и сколько осталось до подарка.

Порог, которого человек не видит, работает вполсилы: «у меня два билета
или три?» — без ответа в боте это гадание, и за четвёртым другом никто не
пойдёт. Поэтому экран показывает ровно три вещи: сколько билетов, сколько
осталось до подарка и сколько дней до конца.

Число считается по тем же правилам, что и таблица для розыгрыша
(app/services/raffle.py), а не по отдельному счётчику: иначе бот показывал
бы одно, а в списке билетов стояло другое — и правым был бы человек.

Кнопка появляется в профиле только пока акция идёт. Раздел, который
показывает «розыгрыш закончился» полгода подряд, — худший из возможных.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.content.emoji import e
from app.core.time import now
from app.domain import raffle as domain
from app.services import raffle as service

# Сколько дней после конца акции экран ещё показывается: розыгрыш проходит
# не в ту же минуту, и человек приходит смотреть свои билеты.
AFTER_DAYS = 3


async def window(settings) -> tuple:
    start = domain.parse_day(str(await settings.get('raffle.start') or ''))
    end = domain.parse_day(str(await settings.get('raffle.end') or ''), end=True)
    return start, end


async def running(settings) -> bool:
    """Идёт ли акция прямо сейчас — по ней и показывается кнопка."""
    start, end = await window(settings)
    if not start or not end:
        return False
    moment = now()
    return start <= moment <= end + _after()


def _after():
    from datetime import timedelta

    return timedelta(days=AFTER_DAYS)


def _tickets(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return 'билет'
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return 'билета'
    return 'билетов'


def _days(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return 'день'
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return 'дня'
    return 'дней'


async def screen(event, c, user: dict, settings) -> None:
    start, end = await window(settings)
    if not start or not end:
        await _answer(event, 'Розыгрыш ещё не объявлен')
        return

    user_id = int(((user or {}).get('user_data') or {}).get('user_id') or 0)
    per_friend = await settings.int('raffle.friend_tickets')
    per_month = await settings.int('raffle.self_per_month')
    mine = await service.for_user(
        c.balance_log, c.users, user_id, start=start, end=end,
        friend_tickets=per_friend, self_per_month=per_month,
        min_months=await settings.int('raffle.min_months'),
        require_active=await settings.flag('raffle.require_active'),
        exclude=await settings.get('raffle.exclude'))

    tickets = mine['tickets']
    need = await settings.int('raffle.bonus_tickets')
    gift_days = await settings.int('raffle.bonus_days')
    left = max(0, (end - now()).days)
    over = now() > end

    lines = [f'<b>{e("cart")} Ваших билетов: {tickets}</b>']
    if tickets:
        parts = []
        if mine['friends']:
            parts.append(f'за друзей — {mine["friend_tickets"]} '
                         f'({mine["friends"]} чел.)')
        if mine['own_tickets']:
            parts.append(f'за свою подписку — {mine["own_tickets"]} '
                         f'({mine["own_months"]} мес.)')
        lines.append('   ' + ', '.join(parts))
    lines.append('')

    if over:
        lines.append('Приём билетов закончен — ждём розыгрыша.')
    elif need and gift_days and tickets < need:
        # Порог считается в билетах, а не в друзьях: друг даёт сразу три, и
        # «осталось два друга» при пороге в три билета — прямая неправда.
        left_tickets = need - tickets
        lines.append(f'{e("gift")} До подарка: ещё '
                     f'<b>{left_tickets} {_tickets(left_tickets)}</b> — '
                     f'и +{gift_days} дней подписки, без всякого розыгрыша.')
    elif need and gift_days:
        lines.append(f'{e("ok")} Подарок ваш: +{gift_days} дней начислим '
                     f'после розыгрыша. Каждый следующий друг — ещё '
                     f'{per_friend} {_tickets(per_friend)}.')
    elif not tickets:
        lines.append(f'Билетов пока нет: {per_friend} {_tickets(per_friend)} '
                     f'даёт новый друг, купивший подписку, и '
                     f'{per_month} {_tickets(per_month)} — каждый месяц '
                     f'вашей собственной.')
    else:
        # Порог выключен — но и молчать нельзя: экран без единой строки
        # после числа выглядит как недоделанный.
        lines.append('Каждый новый друг и каждый месяц подписки — ещё '
                     'билеты. Чем их больше, тем выше шанс.')

    if not over:
        lines.append('')
        lines.append(f'{e("calendar")} До конца акции: <b>{left} {_days(left)}</b>')

    text = (profile_caption(user, f'{e("gift")} Розыгрыш')
            + '\n'.join(lines) + '\n\n'
            + f'<blockquote>{await settings.get("raffle.rules")}</blockquote>')

    kb = InlineKeyboardBuilder()
    if not over:
        kb.row(types.InlineKeyboardButton(
            text=f'{e("referrals")} Позвать друга',
            callback_data=Menu(screen='referrals').pack()))
    await footer(kb, settings, back='profile')

    await render(event, Screen(text=text, markup=kb.as_markup(),
                               image=c.media('referrals')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def _answer(event, text: str) -> None:
    if isinstance(event, types.CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)


def create_router() -> Router:
    router = Router(name='raffle')
    router.callback_query.register(screen, Menu.filter(F.screen == 'raffle'))
    return router
