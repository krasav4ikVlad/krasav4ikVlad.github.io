"""`/rich` — посмотреть, как главное меню выглядело бы Rich Message.

Команда ничего не меняет в боте: это примерка. Сообщение приходит тому,
кто её набрал, с его настоящими данными и рабочими кнопками — нажатие
ведёт в тот же раздел, что и обычное меню.

    /rich        главное меню
    /rich all    все возможности разом, чтобы выбрать нужные

Если клиент или сервер Telegram ещё не умеет Rich Messages, команда
покажет ответ API словами, а не промолчит: именно это и надо выяснить
до того, как переводить на такой вид весь бот.

Два места, где примерка отличается от обычного экрана
─────────────────────────────────────────────────────
* Кастомные эмодзи. У sendRichMessage нет поля parse_mode, и middleware
  сессии (app/bot/middlewares/emoji.py) такое сообщение не трогает —
  значки навешиваются здесь, вызовом decorate().
* Картинка. Она уезжает отдельным списком media, а в HTML стоит ссылкой
  tg://photo?id=. Если Telegram её не примет, экран отправится второй
  попыткой без шапки: вёрстку посмотреть важнее, чем картинку.
"""

from __future__ import annotations

import logging
import re

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InputMediaPhoto, InputRichMessage, InputRichMessageMedia

from app.bot.screens import rich_menu
from app.content.emoji import decorate, e

log = logging.getLogger(__name__)

IMAGE = re.compile(r'<img[^>]*/>')

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
        html, media = rich_menu.showcase(), []
    else:
        html, media = await menu_html(message.from_user.id, c, settings)

    # Отказ запоминается в переменную: имя из `except ... as` после блока
    # уже не существует, а показать его надо в самом конце.
    failure: BaseException | None = None
    try:
        await _send(message, html, media)
        return
    except Exception as exc:      # noqa: BLE001 — ответ API важнее красоты
        failure = exc
        log.warning('rich-сообщение не ушло: %s', exc)

    if media:
        # Чаще всего отказ именно в картинке: она новая в этом API, а
        # вёрстку посмотреть можно и без неё.
        try:
            await _send(message, IMAGE.sub('', html), [])
            await message.answer(
                f'{e("attention")} <b>Картинку Telegram не принял</b>, '
                f'остальное — выше.')
            return
        except Exception as exc:      # noqa: BLE001
            failure = exc
            log.warning('rich-сообщение не ушло и без картинки: %s', exc)

    await message.answer(
        f'{e("cross")} <b>Telegram не принял Rich Message</b>\n'
        f'<code>{_shorten(failure)}</code>\n\n'
        f'<blockquote>Нужны Bot API 10.3+ на стороне Telegram и '
        f'свежий клиент. Если в ответе «method not found» — дело в '
        f'версии aiogram, она должна быть 3.31 и выше.</blockquote>')


def _shorten(exc: BaseException | None) -> str:
    return str(exc)[:600] if exc else 'без объяснения'


async def _send(message: types.Message, html: str, media: list) -> None:
    """Отправка с кастомными эмодзи: middleware сессии сюда не достаёт."""
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage(html=decorate(html),
                                      media=media or None))


async def menu_html(user_id: int, c, settings) -> tuple[str, list]:
    """Главное меню с настоящими данными того, кто позвал, и его картинкой."""
    from app.bot.handlers.raffle import running

    user = await c.users.get(user_id) or {}
    info = user.get('info') or {}
    stats = info.get('ref_stats') or {}

    limit = int(c.users.pick(user, 'vpn.hwidDeviceLimit', 0) or 0)
    devices = f'до {limit}' if limit else ''

    # Та же картинка, что на обычном экране профиля, и через тот же кэш
    # file_id: примерка не должна заливать PNG заново на каждый вызов.
    photo = c.media('profile')
    media = []
    if photo:
        media.append(InputRichMessageMedia(
            id=rich_menu.PHOTO, media=InputMediaPhoto(media=photo.as_input())))

    html = rich_menu.main_menu(
        user,
        balance=int(info.get('balance') or 0),
        friends=len(stats.get('referrals') or []),
        devices=devices,
        email=str(info.get('email') or ''),
        support_url=str(await settings.get('link.support') or ''),
        raffle=await running(settings),
        photo=rich_menu.PHOTO_LINK if media else '')
    return html, media


def register(router: Router) -> None:
    router.message.register(rich_preview, Command('rich'))
