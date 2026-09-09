"""Удаление пользователя из админки: /deluser.

    /deluser 123456789      — по id
    /deluser @username      — по юзернейму

Команда для тестов: удалить твинк и пройти сценарий новичка заново.
Пользовательских данных она не бережёт — именно поэтому здесь два
предохранителя:

* показ того, что будет удалено, и подтверждение кнопкой — одного
  случайного нажатия недостаточно;
* администратора удалить нельзя. Иначе можно снести себе доступ к админке
  вместе с собственным документом.

Опасное действие не должно выглядеть как обычное: экран подтверждения
называет и человека, и число документов, и подписки в панели.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e

log = logging.getLogger(__name__)


def _who(doc: dict) -> str:
    data = doc.get('user_data') or {}
    name = f'@{data["username"]}' if data.get('username') else 'без юзернейма'
    return f'{name} (<code>{data.get("user_id", "?")}</code>)'


async def ask(message: types.Message, command: CommandObject, c) -> None:
    args = (command.args or '').strip()
    if not args:
        await message.answer(
            f'{e("trash")} <b>Удаление пользователя</b>\n\n'
            'Кого удалить? <code>/deluser 123456789</code> или '
            '<code>/deluser @username</code>\n\n'
            '<blockquote>Команда для тестов: стирает человека из базы и его '
            'подписки из панели, чтобы он мог зарегистрироваться заново как '
            'новичок. Восстановить нельзя.</blockquote>')
        return

    target = await c.moderation.find_user(args)
    if not target:
        await message.answer(f'Пользователь <code>{args}</code> не найден.')
        return

    user_id = (target.get('user_data') or {}).get('user_id')
    if user_id in set(c.config.admin_ids):
        await message.answer('Администратора удалить нельзя — вместе с '
                             'документом пропадёт и доступ к админке.')
        return

    found = await c.wipe.preview(user_id)
    vpn = target.get('vpn') or {}
    subscriptions = [u for u in (vpn.get('uuid'), vpn.get('bypass_uuid')) if u]
    balance = ((target.get('info') or {}).get('balance', 0))

    lines = [f'{e("trash")} <b>Удалить пользователя?</b>\n',
             _who(target),
             f'<b>Баланс:</b> <code>{balance}₽</code>',
             f'<b>Подписок в панели:</b> <code>{len(subscriptions)}</code>']
    if found:
        lines.append('\n<b>Записи в базе:</b>')
        lines += [f'• {name}: <code>{count}</code>' for name, count in found.items()]

    lines.append('\n<blockquote>Удалится всё: документ, платежи, подарки, '
                 'использованные промокоды и подписки в панели. Отменить '
                 'нельзя, деньги на балансе сгорят.</blockquote>')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("trash")} Да, удалить',
        callback_data=Adm(act='delusr', a=str(user_id)).pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Отмена', callback_data=Adm(act='main').pack()))

    await message.answer('\n'.join(lines), reply_markup=kb.as_markup())


async def confirm(call: types.CallbackQuery, callback_data: Adm, c) -> None:
    try:
        user_id = int(callback_data.a)
    except (TypeError, ValueError):
        await call.answer('Некорректный id', show_alert=True)
        return

    if user_id in set(c.config.admin_ids):
        await call.answer('Администратора удалить нельзя', show_alert=True)
        return

    result = await c.wipe.wipe(user_id)
    if not result.ok:
        await call.answer('Пользователь уже удалён', show_alert=True)
        return

    log.warning('админ %s удалил пользователя %s', call.from_user.id, user_id)

    lines = [f'{e("ok")} <b>Пользователь <code>{user_id}</code> удалён</b>\n',
             f'<b>Подписок убрано из панели:</b> <code>{result.panel_deleted}</code>']
    if result.panel_failed:
        lines.append(f'{e("attention")} Не удалось убрать: '
                     f'<code>{result.panel_failed}</code> — панель не ответила. '
                     'Проверьте её вручную, иначе повторная регистрация '
                     'упрётся в «short UUID already exists».')
    lines.append(f'<b>Документов удалено:</b> <code>{result.total}</code>')
    lines.append('\nМожно снова нажимать /start с этого аккаунта.')

    await call.message.edit_text('\n'.join(lines), reply_markup=None)
    await call.answer('Удалён')


def register(router: Router) -> None:
    """Подключается к админскому роутеру: фильтр «только админ» уже там."""
    router.message.register(ask, Command('deluser'))
    router.callback_query.register(confirm, Adm.filter(F.act == 'delusr'))
