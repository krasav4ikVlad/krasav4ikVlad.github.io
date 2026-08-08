"""Клавиатуры раздела выплат — и пользовательские, и карточка для админа."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Payout, PayoutAdmin
from app.domain import payout_methods as pm
from app.content.emoji import e


def _btn(text: str, action: str, value: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Payout(action=action, value=value).pack())


def methods_keyboard(methods: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for method in methods:
        kb.row(_btn(f'{e("document")} {pm.method_title(method)}', 'view', method['id']))
    kb.row(_btn(f'{e("plus")} Добавить способ', 'add'))
    return kb


def draft_keyboard(draft: dict) -> InlineKeyboardBuilder:
    """Тип способа, поля под него и сохранение."""
    kb = InlineKeyboardBuilder()
    current = draft.get('type', pm.DEFAULT_TYPE)

    kb.row(*[types.InlineKeyboardButton(
        text=(f'{e("ok")} ' if method.code == current else '') + method.title,
        callback_data=Payout(action='type', value=method.code).pack())
        for method in pm.METHODS])

    for field in pm.fields_of(current):
        filled = str((draft.get('data') or {}).get(field.code) or '').strip()
        mark = e('edit') if filled else e('plus')
        kb.row(_btn(f'{mark} {field.title}', 'field', field.code))

    kb.row(_btn(f'{e("broom")} Очистить', 'clear'), _btn(f'{e("ok")} Добавить', 'save'))
    return kb


def payout_menu_keyboard(methods: list[dict], selected: str) -> InlineKeyboardBuilder:
    """Выбор, куда выводить. Отмечен ровно один способ."""
    kb = InlineKeyboardBuilder()

    def mark(code: str, title: str) -> str:
        return (f'{e("ok")} ' if code == selected else '') + title

    kb.row(_btn(mark(pm.BOT_BALANCE, pm.BOT_BALANCE_TITLE), 'pick', pm.BOT_BALANCE))
    for method in methods:
        kb.row(_btn(mark(method['id'], f'{e("document")} {pm.method_title(method)}'), 'pick', method['id']))

    kb.row(_btn(f'{e("withdraw")} Заказать вывод', 'order'))
    kb.row(_btn(f'{e("settings")} Способы вывода', 'methods'))
    return kb


def _admin_btn(text: str, action: str, user_id: int,
               reason: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text,
        callback_data=PayoutAdmin(action=action, user_id=user_id, reason=reason).pack())


def payout_card_keyboard(user_id: int, to_bot_balance: bool = True) -> types.InlineKeyboardMarkup:
    """Кнопки под заявкой в админ-чате.

    Действие показывается ровно одно и то, которое человек заказал:
    «На баланс» переводит реферальные деньги на обычный баланс внутри бота,
    «Выведено» отмечает перевод по реквизитам наружу. Показывать обе разом
    значит предлагать нажать не ту — а обе необратимы.
    """
    kb = InlineKeyboardBuilder()
    kb.row(_admin_btn(f'{e("refresh")} Обновить', 'refresh', user_id))
    if to_bot_balance:
        kb.row(_admin_btn(f'{e("money")} На баланс', 'balance', user_id))
    else:
        kb.row(_admin_btn(f'{e("ok")} Выведено', 'paid', user_id))
    kb.row(_admin_btn(f'{e("cross")} Отказать', 'reject_ask', user_id))
    return kb.as_markup()


def reject_reasons_keyboard(user_id: int, reasons: dict[str, str]) -> types.InlineKeyboardMarkup:
    """Причины отказа и обязательный шаг назад: «Отказать» нажимают и
    случайно, а отсюда иначе не выйти, не отказав."""
    kb = InlineKeyboardBuilder()
    titles = {'data': 'Неверные реквизиты', 'form': 'Неверный формат данных',
              'min': 'Меньше минимума', 'other': 'Другое'}
    for code in reasons:
        kb.row(_admin_btn(titles.get(code, code), 'reject', user_id, code))
    kb.row(_admin_btn(f'{e("back")} Назад', 'refresh', user_id))
    return kb.as_markup()
