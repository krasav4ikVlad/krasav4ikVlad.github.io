"""Блокировка пользователей из админки.

Две команды и список. Команды, а не только кнопки: банить обычно нужно по
свежей жалобе, когда id уже перед глазами, и лишние три перехода по меню
здесь только мешают.

    /ban 123456789 спам в поддержку     — закрыть бота, подписка работает
    /hardban 123456789 чарджбек          — плюс отключить подписки в панели
    /unban 123456789                     — снять любую блокировку
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.core.time import fmt
from app.content.emoji import e

PAGE_SIZE = 20


def _who(doc: dict) -> str:
    data = doc.get('user_data') or {}
    name = f'@{data["username"]}' if data.get('username') else 'без юзернейма'
    return f'{name} (<code>{data.get("user_id", "?")}</code>)'


async def _ban(message: types.Message, command: CommandObject, c, hard: bool) -> None:
    name = '/hardban' if hard else '/ban'
    args = (command.args or '').split(maxsplit=1)
    if not args:
        await message.answer(f'Кого банить? <code>{name} 123456789 причина</code>')
        return

    target = await c.moderation.find_user(args[0])
    if not target:
        await message.answer(f'Пользователь <code>{args[0]}</code> не найден.')
        return

    user_id = (target.get('user_data') or {}).get('user_id')
    if user_id in set(c.config.admin_ids):
        await message.answer('Администратора забанить нельзя — иначе некому будет разбанить.')
        return

    reason = args[1] if len(args) > 1 else ''
    result = await c.moderation.ban(user_id, message.from_user.id, reason, hard=hard)

    verdict = (f'{e("hardban")} Жёстко заблокирован' if hard
               else f'{e("ban")} Заблокирован')
    text = [f'{verdict} {_who(target)}']
    if reason:
        text.append(f'Причина: {reason}')
    if hard:
        text.append(f'Подписок отключено в панели: <code>{result.disabled}</code>')
        if result.panel_failed:
            text.append(f'{e("attention")} Не удалось отключить: <code>{result.panel_failed}</code> — '
                        'панель не ответила. Блокировка бота уже действует, '
                        'повторите команду, чтобы закрыть доступ.')
    else:
        text.append('<i>Подписка продолжает работать. Отключить — '
                    f'<code>/hardban {user_id}</code></i>')
    await message.answer('\n'.join(text))


async def ban_command(message: types.Message, command: CommandObject, c) -> None:
    await _ban(message, command, c, hard=False)


async def hardban_command(message: types.Message, command: CommandObject, c) -> None:
    await _ban(message, command, c, hard=True)


async def unban_command(message: types.Message, command: CommandObject, c) -> None:
    if not (command.args or '').strip():
        await message.answer('Кого разбанить? <code>/unban 123456789</code>')
        return

    target = await c.moderation.find_user(command.args.strip())
    if not target:
        await message.answer(f'Пользователь <code>{command.args.strip()}</code> не найден.')
        return

    user_id = (target.get('user_data') or {}).get('user_id')
    result = await c.moderation.unban(user_id, message.from_user.id)

    text = [f'{e("ok")} Разблокирован {_who(target)}']
    if result.hard:
        text.append(f'Подписок включено обратно: <code>{result.disabled}</code>')
        if result.panel_failed:
            text.append(f'{e("attention")} Не удалось включить: <code>{result.panel_failed}</code> — '
                        'проверьте панель вручную.')
    await message.answer('\n'.join(text))


async def banned_list(call: types.CallbackQuery, c) -> None:
    from app.admin.panel import edit

    banned = await c.moderation.banned(PAGE_SIZE)
    total = await c.moderation.count()

    kb = InlineKeyboardBuilder()
    lines = [f'<b>{e("ban")} Заблокированные</b>\n\nВсего: <code>{total}</code>\n']

    for doc in banned:
        info = doc.get('moderation') or {}
        mark = f'{e("hardban")} жёстко' if info.get('hard') else f'{e("ban")} обычно'
        lines.append(f'• {_who(doc)} — {mark}, {fmt(info.get("banned_at"))}'
                     + (f'\n  <i>{info["reason"]}</i>' if info.get('reason') else ''))
        kb.row(types.InlineKeyboardButton(
            text=f'{e("ok")} Разбанить {(doc.get("user_data") or {}).get("user_id")}',
            callback_data=Adm(act='unban', a=str((doc.get('user_data') or {}).get('user_id'))).pack()))

    if not banned:
        lines.append('Никто не заблокирован.')
    if total > PAGE_SIZE:
        lines.append(f'\nПоказаны первые {PAGE_SIZE}. '
                     'Остальных ищите командой <code>/unban id</code>.')

    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    await edit(call, '\n'.join(lines), kb)


async def unban_button(call: types.CallbackQuery, callback_data: Adm, c) -> None:
    if callback_data.a.isdigit():
        await c.moderation.unban(int(callback_data.a), call.from_user.id)
        await call.answer(f'Разблокирован {e("ok")}')
    await banned_list(call, c)


def register(router: Router) -> None:
    """Подключается к админскому роутеру: фильтр «только админ» уже стоит там."""
    router.message.register(ban_command, Command('ban'))
    router.message.register(hardban_command, Command('hardban'))
    router.message.register(unban_command, Command('unban'))
    router.callback_query.register(banned_list, Adm.filter(F.act == 'banned'))
    router.callback_query.register(unban_button, Adm.filter(F.act == 'unban'))
