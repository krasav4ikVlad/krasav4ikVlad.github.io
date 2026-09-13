"""Именные ссылки приглашения: выдать, посмотреть, отобрать.

Числовая ссылка `ref_802421217` работает, но по ней нельзя сказать, откуда
пришёл человек. Метка решает ровно это: блогеру выдаётся `ref_vlad`, на
второй канал того же блогера — `ref_vlad_tg`, и в отчёте видно, какая из
них привела людей, а какая нет.

Метка ничего не меняет в самой рефералке: проценты, начисления и выплаты
считаются по человеку, которому она принадлежит. Это адрес, а не тариф.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt
from app.domain import ref_tags as domain

USAGE = (
    f'{e("link")} <b>Именные ссылки приглашения</b>\n\n'
    f'<code>/reftag vlad 802421217</code> — выдать метку человеку\n'
    f'<code>/reftag vlad @username блогер</code> — можно по юзернейму и '
    f'с заметкой\n'
    f'<code>/reftags</code> — все метки и сколько каждая привела\n'
    f'<code>/reftagdel vlad</code> — освободить метку\n\n'
    f'<blockquote>Ссылка станет '
    f'<code>t.me/бот?start=ref_vlad</code> и будет вести на того же '
    f'человека, что и его обычная. Одному человеку можно выдать несколько '
    f'меток — по одной на канал, чтобы видеть, какая работает. '
    f'В метке латиница, цифры и подчёркивание.</blockquote>'
)


async def add(message: types.Message, command, c, settings) -> None:
    """`/reftag <метка> <id|@username> [заметка]` — выдать именную ссылку."""
    parts = (command.args or '').split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(USAGE)
        return

    raw_tag, target = parts[0], parts[1]
    note = parts[2].strip() if len(parts) > 2 else ''

    problem = domain.check(raw_tag)
    if problem:
        await message.answer(f'{e("cross")} {problem}')
        return

    owner = await c.moderation.find_user(target)
    if not owner:
        await message.answer(f'{e("cross")} Пользователь <code>{target}</code> '
                             f'не найден. Он должен хотя бы раз зайти в бота.')
        return

    user_id = (owner.get('user_data') or {}).get('user_id')
    tag = domain.normalize(raw_tag)

    # Занята ли метка, спрашиваем не проверкой, а попыткой занять: между
    # проверкой и вставкой успевает пройти второй вызов.
    if not await c.ref_tags.create(tag, user_id, note, admin_id=message.from_user.id):
        busy = await c.ref_tags.owner(tag)
        await message.answer(
            f'{e("cross")} Метка <code>{tag}</code> уже занята'
            + (f' — она ведёт на <code>{busy}</code>.' if busy else '.')
            + f'\n\nОсвободить: <code>/reftagdel {tag}</code>')
        return

    username = (owner.get('user_data') or {}).get('username')
    bot_username = await settings.get('link.bot_username')

    await message.answer(
        f'{e("ok")} <b>Метка выдана</b>\n\n'
        f'{e("link")} <code>{domain.link(bot_username, tag)}</code>\n'
        f'{e("user")} Ведёт на '
        + (f'@{username} ' if username else '')
        + f'(<code>{user_id}</code>)'
        + (f'\n{e("note")} {note}' if note else '')
        + f'\n\n<blockquote>Человек увидит эту ссылку у себя в разделе '
          f'«Реферальная программа» вместо числовой. Начисления считаются '
          f'как обычно — метка только меняет вид ссылки.</blockquote>')


async def listing(message: types.Message, c, settings) -> None:
    """`/reftags` — какая метка сколько привела."""
    rows = await c.ref_tags.all()
    if not rows:
        await message.answer(f'{e("link")} Именных ссылок пока нет.\n\n{USAGE}')
        return

    bot_username = await settings.get('link.bot_username')
    total = sum(int(row.get('registrations') or 0) for row in rows)

    lines = [f'{e("link")} <b>Именные ссылки</b>',
             f'Всего приведено: <b>{total}</b>', '']
    for row in rows:
        tag = row.get('tag')
        count = int(row.get('registrations') or 0)
        lines.append(f'<code>{tag}</code> — <b>{count}</b> '
                     + ('чел.' if count != 1 else 'чел.'))
        lines.append(f'   владелец <code>{row.get("user_id")}</code>'
                     + (f', {row["note"]}' if row.get('note') else ''))
        lines.append(f'   <code>{domain.link(bot_username, tag)}</code>')
        if row.get('last_at'):
            lines.append(f'   последний переход: {fmt(row["last_at"])}')

    lines.append('')
    lines.append('<blockquote>Метка с нулём приводов — это не поломка, '
                 'а ответ: ссылку либо не опубликовали, либо она не '
                 'работает у аудитории.</blockquote>')
    await message.answer('\n'.join(lines))


async def remove(message: types.Message, command, c, settings) -> None:
    """`/reftagdel <метка>` — освободить метку."""
    tag = domain.normalize(command.args or '')
    if not tag:
        await message.answer(f'{e("cross")} <code>/reftagdel vlad</code>')
        return

    if not await c.ref_tags.remove(tag):
        await message.answer(f'{e("cross")} Метки <code>{tag}</code> нет.')
        return

    await message.answer(
        f'{e("trash")} Метка <code>{tag}</code> освобождена.\n\n'
        f'<blockquote>Уже приглашённые люди остаются за тем, кто их привёл: '
        f'связь хранится в их документе, а не в метке. Перестанет работать '
        f'только сама ссылка — кто перейдёт по ней теперь, зайдёт как '
        f'обычный новый человек.</blockquote>')


async def screen(call: types.CallbackQuery, c, settings) -> None:
    from app.admin.panel import edit

    rows = await c.ref_tags.all(limit=30)
    bot_username = await settings.get('link.bot_username')

    lines = [f'<b>{e("link")} Именные ссылки</b>', '']
    if rows:
        for row in rows:
            lines.append(f'<code>{row.get("tag")}</code> — '
                         f'<b>{int(row.get("registrations") or 0)}</b> чел., '
                         f'владелец <code>{row.get("user_id")}</code>')
        lines.append('')
    else:
        lines.append('Пока ни одной.')
        lines.append('')

    lines.append(f'Выдать: <code>/reftag метка id</code>\n'
                 f'Подробнее со ссылками: <code>/reftags</code>')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    await edit(call, '\n'.join(lines), kb)


def register(router: Router) -> None:
    router.message.register(add, Command('reftag'))
    router.message.register(listing, Command('reftags'))
    router.message.register(remove, Command('reftagdel'))
    router.callback_query.register(screen, Adm.filter(F.act == 'reftags'))
