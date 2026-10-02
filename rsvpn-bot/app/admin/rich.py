"""`/rich` — посмотреть, как главное меню выглядело бы Rich Message.

Команда ничего не меняет в боте: это примерка. Сообщение приходит тому,
кто её набрал, с его настоящими данными и рабочими кнопками — нажатие
ведёт в тот же раздел, что и обычное меню.

    /rich        главное меню
    /rich all    все возможности разом, чтобы выбрать нужные

Если клиент или сервер Telegram ещё не умеет Rich Messages, команда
покажет ответ API словами, а не промолчит: именно это и надо выяснить
до того, как переводить на такой вид весь бот.
"""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InputRichMessage

from app.bot.screens import rich_menu
from app.content.emoji import e, plain

log = logging.getLogger(__name__)

HELP = (
    f'{e("design")} <b>Примерка Rich Message</b>\n\n'
    f'<code>/rich</code> — главное меню на новый лад\n'
    f'<code>/rich all</code> — все элементы разом\n\n'
    f'<blockquote>Это одно текстовое сообщение: его можно копировать, '
    f'переводить и читать с экрана — в отличие от картинки. Кнопки внутри '
    f'настоящие и ведут в те же разделы.</blockquote>'
)


async def rich_preview(message: types.Message, command: CommandObject, c,
                       settings) -> None:
    what = (command.args or '').strip().lower()
    if what in ('help', '?'):
        await message.answer(HELP)
        return

    if what in ('all', 'всё', 'все'):
        html = rich_menu.showcase()
    else:
        html = await menu_html(message.from_user.id, c, settings)

    try:
        # Кастомные эмодзи — как у людей, а не как в админке: примерка
        # должна показывать то, что увидит человек.
        with plain(False):
            await message.bot.send_rich_message(
                chat_id=message.chat.id,
                rich_message=InputRichMessage(html=html))
    except Exception as exc:      # noqa: BLE001 — ответ API важнее красоты
        log.warning('rich-сообщение не ушло: %s', exc)
        await message.answer(
            f'{e("cross")} <b>Telegram не принял Rich Message</b>\n'
            f'<code>{str(exc)[:600]}</code>\n\n'
            f'<blockquote>Нужны Bot API 10.3+ на стороне Telegram и '
            f'свежий клиент. Если в ответе «method not found» — дело в '
            f'версии aiogram, она должна быть 3.31 и выше.</blockquote>')


async def menu_html(user_id: int, c, settings) -> str:
    """Главное меню с настоящими данными того, кто позвал."""
    from app.bot.handlers.raffle import running

    user = await c.users.get(user_id) or {}
    info = user.get('info') or {}
    stats = info.get('ref_stats') or {}

    limit = int(c.users.pick(user, 'vpn.hwidDeviceLimit', 0) or 0)
    devices = f'до {limit}' if limit else ''

    return rich_menu.main_menu(
        user,
        balance=int(info.get('balance') or 0),
        friends=len(stats.get('referrals') or []),
        devices=devices,
        email=str(info.get('email') or ''),
        support_url=str(await settings.get('link.support') or ''),
        raffle=await running(settings))


def register(router: Router) -> None:
    router.message.register(rich_preview, Command('rich'))
