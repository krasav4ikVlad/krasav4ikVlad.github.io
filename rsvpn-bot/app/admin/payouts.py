"""Решения по заявкам на вывод — кнопки под карточкой в админ-чате.

Сервис (services/payouts.py) уже умел `to_balance`, `paid_externally` и
`reject`, но вызывать их было неоткуда: карточка с кнопками не отправлялась.
Здесь ровно связка «нажали кнопку → сервис → сообщить человеку».

Карточка правится на месте (edit_reply_markup + подпись о решении), поэтому
двое админов не обработают одну заявку дважды, а в теме остаётся история.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types

from app.bot.callbacks import PayoutAdmin
from app.bot.keyboards.payouts import payout_card_keyboard, reject_reasons_keyboard
from app.content.emoji import e, plain
from app.core.time import fmt, now, parse_dt
from app.domain import payout_methods as pm
from app.services.payouts import REJECT_REASONS

log = logging.getLogger(__name__)



# ── карточка заявки ─────────────────────────────────────────────────────────
#
# Собирается одной функцией и при отправке, и по кнопке «Обновить». Иначе
# два вида одной карточки разъезжаются: человек за это время мог пополнить
# баланс или сменить способ, и решение принималось бы по старым цифрам.

async def card_text(c, user_id: int) -> str:
    user = await c.users.get(user_id) or {}
    data = user.get('user_data') or {}
    info = user.get('info') or {}
    stats = info.get('ref_stats') or {}

    name = f'@{data["username"]}' if data.get('username') else 'без юзернейма'
    joined = data.get('date_joined')
    # в старой базе дата регистрации лежит строкой, в новой — датой
    joined_text = joined if isinstance(joined, str) else fmt(parse_dt(joined), '%d.%m.%Y %H:%M:%S')

    selected = stats.get('payout_selected') or pm.BOT_BALANCE
    method = c.payouts.describe(stats, selected)

    lines = [f'{e("payout")} <b>Заявка на вывод</b>', '',
             f'{e("user")} {name} (<code>{user_id}</code>)',
             f'{e("calendar")} Регистрация: {joined_text or "—"}']
    if data.get('utm'):
        lines.append(f'{e("link")} UTM: <code>{data["utm"]}</code>')

    lines += ['',
              f'{e("money")} Обычный баланс: <b>{int(info.get("balance", 0) or 0)}₽</b>',
              f'{e("gift")} Реферальный баланс: '
              f'<b>{int(stats.get("withdrawable", 0) or 0)}₽</b>',
              '', f'Способ: {method}',
              '', f'<i>обновлено в {now().strftime("%H:%M:%S")}</i>']
    return '\n'.join(lines)


async def card_markup(c, user_id: int):
    """Кнопки зависят от способа: «На баланс» и «Выведено» — разные операции,
    и показывать обе значит предлагать нажать не ту."""
    user = await c.users.get(user_id, {'info.ref_stats.payout_selected': 1}) or {}
    selected = (c.users.pick(user, 'info.ref_stats.payout_selected')
                or pm.BOT_BALANCE)
    return payout_card_keyboard(user_id, to_bot_balance=selected == pm.BOT_BALANCE)


async def show_card(call: types.CallbackQuery, c, user_id: int) -> None:
    try:
        await call.message.edit_text(await card_text(c, user_id),
                                     reply_markup=await card_markup(c, user_id))
    except Exception:      # «message is not modified» — цифры не изменились
        pass


async def refresh(call: types.CallbackQuery, callback_data: PayoutAdmin, c) -> None:
    """Она же «Назад» с экрана причин отказа: возвращает исходную карточку."""
    await show_card(call, c, callback_data.user_id)
    await call.answer('Обновлено')


async def _tell_user(bot, user_id: int, text: str) -> None:
    """Человек должен узнать о решении. Заблокировал бота — не наша беда."""
    try:
        # хендлер админский, а адресат — обычный пользователь: значки ему
        # положены такие же, как везде в боте
        with plain(False):
            await bot.send_message(chat_id=user_id, text=text)
    except Exception as exc:
        log.warning('не удалось сообщить %s о решении по выплате: %s', user_id, exc)


async def _close_card(call: types.CallbackQuery, verdict: str) -> None:
    who = call.from_user.username or call.from_user.id
    try:
        await call.message.edit_text(
            f'{call.message.html_text}\n\n<b>{verdict}</b> — @{who}', reply_markup=None)
    except Exception:      # карточка уже отредактирована другим админом
        pass


async def to_balance(call: types.CallbackQuery, callback_data: PayoutAdmin, c) -> None:
    amount = await c.payouts.to_balance(callback_data.user_id, call.from_user.id)
    if amount < 0:
        await call.answer('Баланс изменился, откройте заявку заново', show_alert=True)
        return

    balance = int(c.users.pick(await c.users.get(callback_data.user_id) or {},
                               'info.balance', 0) or 0)
    await _tell_user(call.bot, callback_data.user_id,
                     f'{e("ok")} <b>Заявка на вывод исполнена</b>\n\n'
                     f'{amount}₽ переведены на ваш баланс в боте.\n'
                     f'<b>Баланс:</b> <code>{balance}₽</code>')
    await _close_card(call, f'{e("money")} Переведено на баланс: {amount}₽')
    await call.answer(f'Переведено {e("ok")}')


async def paid_externally(call: types.CallbackQuery, callback_data: PayoutAdmin, c) -> None:
    amount = await c.payouts.paid_externally(callback_data.user_id, call.from_user.id)
    if amount < 0:
        await call.answer('Баланс изменился, откройте заявку заново', show_alert=True)
        return

    await _tell_user(call.bot, callback_data.user_id,
                     f'{e("ok")} <b>Заявка на вывод исполнена</b>\n\n'
                     f'{amount}₽ отправлены по указанным вами реквизитам.\n'
                     'Деньги поступят в течение пары часов.')
    await _close_card(call, f'{e("ok")} Выведено: {amount}₽')
    await call.answer(f'Отмечено {e("ok")}')


async def ask_reason(call: types.CallbackQuery, callback_data: PayoutAdmin) -> None:
    await call.message.edit_reply_markup(
        reply_markup=reject_reasons_keyboard(callback_data.user_id, REJECT_REASONS))
    await call.answer()


async def reject(call: types.CallbackQuery, callback_data: PayoutAdmin, c) -> None:
    text = await c.payouts.reject(callback_data.user_id, callback_data.reason,
                                  call.from_user.id)
    await _tell_user(call.bot, callback_data.user_id,
                     f'{e("cross")} Заявка на вывод отклонена: {text}\n\n'
                     'Деньги остались на реферальном балансе — исправьте реквизиты '
                     'и оформите заявку заново.')
    await _close_card(call, f'{e("cross")} Отказ ({callback_data.reason})')
    await call.answer('Отказано')


def register(router: Router) -> None:
    """Подключается к админскому роутеру: фильтр «только админ» уже стоит там."""
    router.callback_query.register(refresh, PayoutAdmin.filter(F.action == 'refresh'))
    router.callback_query.register(to_balance, PayoutAdmin.filter(F.action == 'balance'))
    router.callback_query.register(paid_externally, PayoutAdmin.filter(F.action == 'paid'))
    router.callback_query.register(ask_reason, PayoutAdmin.filter(F.action == 'reject_ask'))
    router.callback_query.register(reject, PayoutAdmin.filter(F.action == 'reject'))
