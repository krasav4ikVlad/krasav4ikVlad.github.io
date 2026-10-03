"""Сброс бесплатного периода из админки.

Бесплатный период выдаётся один раз и помечается в документе навсегда.
Иногда это надо отменить: перезапустили акцию, чинили панель и триалы
сгорели впустую, решили ещё раз позвать вернувшихся.

Аудитории — те же, что у рассылки (app/domain/segments.py), поэтому «на
триале» здесь и «на триале» там означают одно и то же множество людей.

Экран подтверждения обязателен и показывает число: сброс на всю базу —
операция на десятки тысяч документов, и промахнуться кнопкой мимо неё
слишком легко.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.domain.segments import AUDIENCES, audience_query

log = logging.getLogger(__name__)


def _btn(text: str, act: str, a: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=Adm(act=act, a=a).pack())


async def menu(call: types.CallbackQuery, c, settings) -> None:
    """Сколько людей в каждой аудитории уже потратили бесплатный период."""
    kb = InlineKeyboardBuilder()
    lines = [f'<b>{e("trial")} Сброс бесплатного периода</b>\n']

    for code, (title, _) in AUDIENCES.items():
        used = await c.trial.claimed_count(audience_query(code))
        lines.append(f'• {title}: <b>{used}</b>')
        kb.row(_btn(f'{title} — {used}', 'trask', code))

    lines.append('\nЧисло — сколько уже брали период и получат право взять '
                 'заново. Действующая подписка при этом не трогается: пока '
                 'она работает, бесплатный период всё равно не выдаётся.')
    kb.row(_btn(f'{e("back")} Назад', 'main'))

    await call.message.edit_text('\n'.join(lines), reply_markup=kb.as_markup())
    await call.answer()


async def ask(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    audience = callback_data.a
    title = AUDIENCES.get(audience, ('?', ()))[0]
    used = await c.trial.claimed_count(audience_query(audience))

    if not used:
        await call.answer('В этой аудитории бесплатный период никто не брал',
                          show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("ok")} Да, сбросить ({used})', 'trgo', audience))
    kb.row(_btn(f'{e("back")} Отмена', 'trial'))

    await call.message.edit_text(
        f'<b>{e("trial")} Сброс бесплатного периода</b>\n\n'
        f'Аудитория: {title}\nСбросим у <code>{used}</code> человек.\n\n'
        '<blockquote>После сброса каждый сможет взять бесплатный период ещё '
        'раз — на тех же условиях, что и новичок (подписка на канал, если '
        'она включена). У кого подписка сейчас действует, ничего не '
        'изменится, пока она не закончится.</blockquote>',
        reply_markup=kb.as_markup())
    await call.answer()


async def run(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    audience = callback_data.a
    reset = await c.trial.reset(audience_query(audience))
    log.info('админ %s сбросил бесплатный период: аудитория=%s, людей=%s',
             call.from_user.id, audience, reset)

    await call.answer(f'Сброшено: {reset}')
    await menu(call, c, settings)


def register(router: Router) -> None:
    router.callback_query.register(menu, Adm.filter(F.act == 'trial'))
    router.callback_query.register(ask, Adm.filter(F.act == 'trask'))
    router.callback_query.register(run, Adm.filter(F.act == 'trgo'))
