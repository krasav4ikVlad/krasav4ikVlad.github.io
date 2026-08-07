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
from app.bot.keyboards.payouts import reject_reasons_keyboard
from app.services.payouts import REJECT_REASONS
from app.content.emoji import e

log = logging.getLogger(__name__)


async def _tell_user(bot, user_id: int, text: str) -> None:
    """Человек должен узнать о решении. Заблокировал бота — не наша беда."""
    try:
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

    await _tell_user(call.bot, callback_data.user_id,
                     f'{e("money")} Реферальные {amount}₽ переведены на баланс бота.')
    await _close_card(call, f'{e("money")} Переведено на баланс: {amount}₽')
    await call.answer(f'Переведено {e("ok")}')


async def paid_externally(call: types.CallbackQuery, callback_data: PayoutAdmin, c) -> None:
    amount = await c.payouts.paid_externally(callback_data.user_id, call.from_user.id)
    if amount < 0:
        await call.answer('Баланс изменился, откройте заявку заново', show_alert=True)
        return

    await _tell_user(call.bot, callback_data.user_id,
                     f'{e("ok")} Выплата {amount}₽ отправлена по указанным вами реквизитам.')
    await _close_card(call, f'{e("ok")} Выплачено вручную: {amount}₽')
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
    router.callback_query.register(to_balance, PayoutAdmin.filter(F.action == 'balance'))
    router.callback_query.register(paid_externally, PayoutAdmin.filter(F.action == 'paid'))
    router.callback_query.register(ask_reason, PayoutAdmin.filter(F.action == 'reject_ask'))
    router.callback_query.register(reject, PayoutAdmin.filter(F.action == 'reject'))
