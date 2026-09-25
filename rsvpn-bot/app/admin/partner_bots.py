"""Партнёрские боты: подключить, посмотреть, отключить.

Партнёр рекламирует своего бота, а не ссылку на нашего. Ссылку теряют — её
вырезают при пересылке, её не набирают руками, по ней не приходят те, кто
нашёл бота поиском. Имя бота, наоборот, и есть то, что запоминают, поэтому
любой путь к нему несёт метку партнёра.

Партнёр заводит бота у @BotFather сам и присылает токен. Токен — ключ к
чужому боту, поэтому целиком он нигде не показывается.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt
from app.domain import ref_tags

USAGE = (
    f'{e("friends")} <b>Партнёрские боты</b>\n\n'
    f'<code>/partnerbot 123456:AA... vlad</code> — подключить бота под метку\n'
    f'<code>/partnerbots</code> — все боты и сколько каждый привёл\n'
    f'<code>/partnerbotdel @vladvpn_bot</code> — отключить\n\n'
    f'<blockquote>Партнёр создаёт бота у @BotFather и присылает вам токен. '
    f'Метку перед этим выдайте командой <code>/reftag</code> — бот будет '
    f'вести в основного бота с ней, и приведённые засчитаются партнёру.\n\n'
    f'Смысл в том, что рекламируется имя бота, а не ссылка: ссылку теряют '
    f'при пересылке и не набирают руками, а к боту придут любым '
    f'путём.</blockquote>'
)

ERRORS = {
    'bad_token': 'Это не похоже на токен. У BotFather он вида '
                 '<code>123456789:AAH...</code>.',
    'telegram': 'Telegram не принял токен',
    'exists': 'Такой бот уже подключён',
    'no_base': 'Не задан адрес приёма вебхуков — /admin → Платёжные методы → '
               '«Адрес приёма вебхуков». Без него Telegram некуда слать '
               'сообщения партнёрского бота.',
    'webhook': 'Токен верный, но вебхук не поставился',
}


async def connect(message: types.Message, command, c, settings) -> None:
    """`/partnerbot <токен> <метка>` — подключить бота партнёра."""
    parts = (command.args or '').split()
    if len(parts) < 2:
        await message.answer(USAGE)
        return

    token, raw_tag = parts[0], parts[1]
    tag = ref_tags.normalize(raw_tag)

    owner = await c.ref_tags.owner(tag)
    if not owner:
        await message.answer(
            f'{e("cross")} Метки <code>{tag}</code> нет.\n\n'
            f'<blockquote>Сначала выдайте её партнёру: '
            f'<code>/reftag {tag} id_партнёра</code>. Бот без метки вёл бы в '
            f'основного бота просто так, и приведённые никому не '
            f'засчитались бы.</blockquote>')
        return

    result = await c.partner_bots.connect(token, tag, owner)
    if not result.get('ok'):
        reason = ERRORS.get(result.get('reason'), 'Не вышло')
        note = result.get('note')
        await message.answer(f'{e("cross")} {reason}'
                             + (f': <code>{note}</code>' if note else '.'))
        return

    username = result['username']
    await message.answer(
        f'{e("ok")} <b>Бот партнёра подключён</b>\n\n'
        f'{e("support")} @{username}\n'
        f'{e("link")} Метка: <code>{tag}</code>, владелец '
        f'<code>{owner}</code>\n\n'
        f'<blockquote>Отдайте партнёру ссылку <code>t.me/{username}</code> — '
        f'её и рекламировать. Любой, кто нажмёт там кнопку, придёт в '
        f'основного бота с меткой <code>{tag}</code>.\n\n'
        f'Текст и кнопку бота меняйте в /admin → Партнёрские боты — '
        f'у всех партнёрских ботов они общие.</blockquote>')


async def listing(message: types.Message, c, settings) -> None:
    """`/partnerbots` — кто подключён и сколько привёл."""
    rows = await c.partner_bots_repo.all()
    if not rows:
        await message.answer(f'{e("friends")} Партнёрских ботов пока нет.\n\n{USAGE}')
        return

    lines = [f'{e("friends")} <b>Партнёрские боты</b>', '']
    for row in rows:
        starts = int(row.get('starts') or 0)
        lines.append(f'@{row.get("username")} — <b>{starts}</b> запусков')
        lines.append(f'   метка <code>{row.get("tag")}</code>, владелец '
                     f'<code>{row.get("user_id")}</code>')
        if row.get('last_at'):
            lines.append(f'   последний: {fmt(row["last_at"])}')

    lines.append('')
    lines.append('<blockquote>«Запусков» — сколько раз нажали /start в боте '
                 'партнёра. Сколько из них дошло до основного бота, видно в '
                 '<code>/reftags</code> по той же метке: разница между двумя '
                 'числами и есть те, кто не нажал кнопку.</blockquote>')
    await message.answer('\n'.join(lines))


async def disconnect(message: types.Message, command, c, settings) -> None:
    """`/partnerbotdel @bot` — отключить бота партнёра."""
    target = (command.args or '').strip()
    if not target:
        await message.answer(f'{e("cross")} <code>/partnerbotdel @vladvpn_bot</code>')
        return

    found = await c.partner_bots.disconnect(target)
    if not found:
        await message.answer(f'{e("cross")} Такого бота нет.')
        return

    await message.answer(
        f'{e("trash")} @{found.get("username")} отключён — вебхук снят, бот '
        f'больше не отвечает.\n\n'
        f'<blockquote>Метка <code>{found.get("tag")}</code> осталась: ссылка '
        f'<code>ref_{found.get("tag")}</code> работает, и уже приведённые '
        f'люди остаются за партнёром. Убрать и метку — '
        f'<code>/reftagdel {found.get("tag")}</code>.</blockquote>')


async def screen(call: types.CallbackQuery, c, settings) -> None:
    from app.admin.panel import edit

    rows = await c.partner_bots_repo.all(limit=30)

    lines = [f'<b>{e("friends")} Партнёрские боты</b>', '']
    if rows:
        for row in rows:
            lines.append(f'@{row.get("username")} — '
                         f'<b>{int(row.get("starts") or 0)}</b> запусков, '
                         f'метка <code>{row.get("tag")}</code>')
        lines.append('')
    else:
        lines.append('Пока ни одного.')
        lines.append('')

    lines.append('Подключить: <code>/partnerbot токен метка</code>\n'
                 'Подробнее: <code>/partnerbots</code>')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    await edit(call, '\n'.join(lines), kb)


def register(router: Router) -> None:
    router.message.register(connect, Command('partnerbot'))
    router.message.register(listing, Command('partnerbots'))
    router.message.register(disconnect, Command('partnerbotdel'))
    router.callback_query.register(screen, Adm.filter(F.act == 'partnerbots'))
