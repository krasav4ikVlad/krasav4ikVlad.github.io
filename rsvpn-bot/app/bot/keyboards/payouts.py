"""Клавиатуры раздела выплат — и пользовательские, и карточка для админа."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Payout, PayoutAdmin
from app.domain import payout_methods as pm


def _btn(text: str, action: str, value: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Payout(action=action, value=value).pack())


def methods_keyboard(methods: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for method in methods:
        kb.row(_btn(f'📄 {pm.method_title(method)}', 'view', method['id']))
    kb.row(_btn('➕ Добавить способ', 'add'))
    return kb


def draft_keyboard(draft: dict) -> InlineKeyboardBuilder:
    """Тип способа, поля под него и сохранение."""
    kb = InlineKeyboardBuilder()
    current = draft.get('type', pm.DEFAULT_TYPE)

    kb.row(*[types.InlineKeyboardButton(
        text=('✅ ' if method.code == current else '') + method.title,
        callback_data=Payout(action='type', value=method.code).pack())
        for method in pm.METHODS])

    for field in pm.fields_of(current):
        filled = str((draft.get('data') or {}).get(field.code) or '').strip()
        kb.row(_btn(f'{"✏️" if filled else "➕"} {field.title}', 'field', field.code))

    kb.row(_btn('🧹 Очистить', 'clear'), _btn('✅ Добавить', 'save'))
    return kb


def payout_menu_keyboard(methods: list[dict], selected: str) -> InlineKeyboardBuilder:
    """Выбор, куда выводить. Отмечен ровно один способ."""
    kb = InlineKeyboardBuilder()

    def mark(code: str, title: str) -> str:
        return ('✅ ' if code == selected else '') + title

    kb.row(_btn(mark(pm.BOT_BALANCE, pm.BOT_BALANCE_TITLE), 'pick', pm.BOT_BALANCE))
    for method in methods:
        kb.row(_btn(mark(method['id'], f'📄 {pm.method_title(method)}'), 'pick', method['id']))

    kb.row(_btn('📤 Заказать вывод', 'order'))
    kb.row(_btn('⚙️ Способы вывода', 'methods'))
    return kb


def payout_card_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    """Кнопки под заявкой в админ-чате."""
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text='💰 На баланс бота',
        callback_data=PayoutAdmin(action='balance', user_id=user_id).pack()))
    kb.row(types.InlineKeyboardButton(
        text='✅ Выплачено вручную',
        callback_data=PayoutAdmin(action='paid', user_id=user_id).pack()))
    kb.row(types.InlineKeyboardButton(
        text='❌ Отказать',
        callback_data=PayoutAdmin(action='reject_ask', user_id=user_id).pack()))
    return kb.as_markup()


def reject_reasons_keyboard(user_id: int, reasons: dict[str, str]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    titles = {'data': 'Неверные реквизиты', 'form': 'Неверный формат данных',
              'min': 'Меньше минимума', 'other': 'Другое'}
    for code in reasons:
        kb.row(types.InlineKeyboardButton(
            text=titles.get(code, code),
            callback_data=PayoutAdmin(action='reject', user_id=user_id, reason=code).pack()))
    return kb.as_markup()
