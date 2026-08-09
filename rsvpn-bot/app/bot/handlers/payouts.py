"""Вывод реферального баланса: реквизиты и заявка.

Перенос из start.py (блок `menu:withdrawable_*`, ≈200 строк на 11 веток
одного `if`). Здесь то же самое разложено по хендлерам, а вся работа с
документом ушла в PayoutService — экран только показывает и спрашивает.

Почему это отдельный файл, а не часть referrals.py: реквизиты — это ввод
персональных данных с состоянием (черновик, поле за полем), и мешать его
со статистикой рефералов значит получить ещё один файл на 500 строк.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu, Payout
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.keyboards.payouts import (draft_keyboard, methods_keyboard,
                                       payout_confirm_keyboard,
                                       payout_menu_keyboard)
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.domain import payout_methods as pm
from app.content.emoji import e

PAYOUT_MESSAGES = {
    'below_min': 'Минимальная сумма вывода — {minimum}₽.',
    'pending': 'Заявка уже в обработке. Дождитесь её завершения.',
    'cooldown': 'Заявку можно оформлять раз в {cooldown} ч. Осталось ждать {wait} ч.',
    'disabled': 'Вывод временно недоступен.',
}


class MethodInput(StatesGroup):
    value = State()


# ── список способов ─────────────────────────────────────────────────────────
async def methods_screen(event, c, user: dict, settings, note: str = ''):
    methods = await c.payouts.methods(event.from_user.id)

    body = ('\n'.join(f'• {pm.method_title(m)}' for m in methods)
            if methods else 'Пока не добавлено ни одного способа.')
    text = (profile_caption(user, f'{e("payout")} Способы вывода')
            + f'<b>Сохранённые способы:</b>\n{body}\n\n'
            + f'<blockquote>{note or "Реквизиты нужны, чтобы мы знали, куда перечислить деньги. Без них выплата возможна только на баланс бота."}</blockquote>')

    kb = methods_keyboard(methods)
    await footer(kb, settings, back='payout')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


# ── черновик нового способа ─────────────────────────────────────────────────
async def draft_screen(event, c, user: dict, settings, note: str = ''):
    draft = await c.payouts.draft(event.from_user.id)
    data = draft.get('data') or {}

    lines = [f'• {f.title}: <code>{pm.format_value(f.code, data.get(f.code, ""))}</code>'
             for f in pm.fields_of(draft.get('type', ''))]
    missing = pm.missing_fields(draft)
    hint = (note or (f'Не заполнено: {", ".join(f.title for f in missing)}'
                     if missing else f'{e("ok")} Всё готово. Нажмите «Добавить».'))

    text = (profile_caption(user, f'{e("plus")} Новый способ вывода')
            + f'<b>Тип:</b> <code>{pm.method_title(draft)}</code>\n'
            + '\n'.join(lines) + '\n\n'
            + f'<blockquote>{hint}</blockquote>')

    kb = draft_keyboard(draft)
    await footer(kb, settings, back='payout')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def add_method(call: types.CallbackQuery, state: FSMContext, c, user: dict, settings):
    await state.clear()
    await draft_screen(call, c, user, settings)


async def set_type(call: types.CallbackQuery, callback_data: Payout, state: FSMContext,
                   c, user: dict, settings):
    await state.clear()
    await c.payouts.set_draft_type(call.from_user.id, callback_data.value)
    await draft_screen(call, c, user, settings)


async def ask_field(call: types.CallbackQuery, callback_data: Payout, state: FSMContext,
                    c, settings):
    draft = await c.payouts.draft(call.from_user.id)
    field = next((f for f in pm.fields_of(draft.get('type', ''))
                  if f.code == callback_data.value), None)
    if field is None:
        await call.answer('Это поле больше не нужно', show_alert=True)
        return

    await state.set_state(MethodInput.value)
    await state.update_data(field=field.code)

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Payout(action='add').pack()))
    await call.message.answer(
        f'{e("edit")} <b>{field.title}</b>\n\nОтправьте значение сообщением.\n\n'
        f'<blockquote>{field.hint}</blockquote>', reply_markup=kb.as_markup())
    await call.answer()


async def save_field(message: types.Message, state: FSMContext, c, user: dict, settings):
    data = await state.get_data()
    value = (message.text or '').strip()

    if not value or len(value) > 128:
        await message.answer('Слишком длинное или пустое значение, попробуйте ещё раз.')
        return

    await c.payouts.set_draft_field(message.from_user.id, data.get('field', ''), value)
    await state.clear()
    await draft_screen(message, c, await c.users.get(message.from_user.id), settings,
                       note='Сохранено. Заполните остальные поля или нажмите «Добавить».')


async def clear_draft(call: types.CallbackQuery, state: FSMContext, c, user: dict, settings):
    await state.clear()
    await c.payouts.clear_draft(call.from_user.id)
    await draft_screen(call, c, user, settings, note='Черновик очищен.')


async def save_method(call: types.CallbackQuery, state: FSMContext, c, user: dict, settings):
    await state.clear()
    ok, problem = await c.payouts.add_method(call.from_user.id)
    if not ok:
        await call.answer(problem, show_alert=True)
        return

    await call.answer(f'Способ сохранён {e("ok")}')
    await methods_screen(call, c, await c.users.get(call.from_user.id), settings,
                         note='Способ добавлен и выбран для следующей выплаты.')


# ── просмотр и удаление ─────────────────────────────────────────────────────
async def view_method(call: types.CallbackQuery, callback_data: Payout, c, user: dict,
                      settings):
    method = pm.find(await c.payouts.methods(call.from_user.id), callback_data.value)
    if method is None:
        await call.answer('Способ не найден', show_alert=True)
        await methods_screen(call, c, user, settings)
        return

    text = (profile_caption(user, f'{e("payout")} Способ вывода')
            + pm.format_details(method) + '\n\n'
            + '<blockquote>Номер карты показан частично — так он не попадёт '
              'в чужие руки со скриншота.</blockquote>')

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("trash")} Удалить',
        callback_data=Payout(action='delete', value=method['id']).pack()))
    await footer(kb, settings, back='payout')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    await call.answer()


async def delete_method(call: types.CallbackQuery, callback_data: Payout, c, user: dict,
                        settings):
    await c.payouts.delete_method(call.from_user.id, callback_data.value)
    await call.answer('Способ удалён')
    await methods_screen(call, c, await c.users.get(call.from_user.id), settings)


# ── заявка ──────────────────────────────────────────────────────────────────
async def payout_note(settings) -> str:
    """Подпись с минимумами. Суммы подставляются из настроек, а не вписаны.

    Вручную вписанное число живёт своей жизнью: минимум на баланс бота
    подняли до 500₽, а в подписи он был указан только для СБП — люди
    оформляли заявку и получали отказ.
    """
    template = str(await settings.get('payout.note') or '')
    values = {
        'min_balance': await settings.int('payout.min_withdraw'),
        'min_sbp': await settings.int('payout.min_sbp'),
        'min_card': await settings.int('payout.min_card'),
        'min_crypto': await settings.int('payout.min_crypto_usd'),
        'cooldown': await settings.int('payout.cooldown_hours'),
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        # в подписи опечатка в плейсхолдере — текст всё равно должен дойти
        return template


async def payout_menu(event, c, user: dict, settings, note: str = ''):
    user_id = event.from_user.id
    stats = c.users.pick(user, 'info.ref_stats', {}) or {}
    methods = await c.payouts.methods(user_id)
    selected = stats.get('payout_selected') or pm.BOT_BALANCE

    text = (profile_caption(user, f'{e("withdraw")} Вывод средств')
            + f'<b>{e("payout")} Доступно к выводу:</b> <code>{stats.get("withdrawable", 0)}₽</code>\n'
            + f'<b>{e("document")} Куда:</b> <code>{pm.selected_title(methods, selected)}</code>\n\n'
            + f'<blockquote>{note or await payout_note(settings)}</blockquote>')

    kb = payout_menu_keyboard(methods, selected)
    await footer(kb, settings, back='referrals')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def pick_method(call: types.CallbackQuery, callback_data: Payout, c, user: dict,
                      settings):
    await c.payouts.select_method(call.from_user.id, callback_data.value)
    await payout_menu(call, c, await c.users.get(call.from_user.id), settings)


async def _refuse(call: types.CallbackQuery, result, settings) -> None:
    template = PAYOUT_MESSAGES.get(result.reason, 'Заявку оформить не удалось.')
    await call.answer(template.format(
        minimum=await settings.int('payout.min_withdraw'),
        cooldown=await settings.int('payout.cooldown_hours'),
        wait=result.wait_hours), show_alert=True)


# Подтверждение стоит здесь не «на всякий случай»: следующую заявку можно
# подать только через сутки, а «Заказать вывод» — обычная кнопка в списке,
# по которой промахиваются. Человек узнаёт про паузу уже после того, как
# отправил заявку не туда, и сутки ждёт из-за одного лишнего касания.
async def confirm(call: types.CallbackQuery, c, user: dict, settings):
    result = await c.payouts.check(call.from_user.id)
    if not result.ok:
        await _refuse(call, result, settings)
        return

    methods_list = await c.payouts.methods(call.from_user.id)
    cooldown = await settings.int('payout.cooldown_hours')

    text = (profile_caption(user, f'{e("withdraw")} Подтверждение вывода')
            + f'<b>{e("payout")} Сумма:</b> <code>{result.amount}₽</code>\n'
            + f'<b>{e("document")} Куда:</b> '
              f'<code>{pm.selected_title(methods_list, result.method)}</code>\n\n'
            + '<blockquote>Проверьте, что способ выбран правильно.'
            + (f' Следующую заявку можно будет оформить только через '
               f'{cooldown} ч.' if cooldown else '')
            + '</blockquote>')

    kb = payout_confirm_keyboard()
    await footer(kb, settings, back='payout')
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    await call.answer()


async def order(call: types.CallbackQuery, c, user: dict, settings):
    result = await c.payouts.request(call.from_user.id)

    if not result.ok:
        await _refuse(call, result, settings)
        await payout_menu(call, c, await c.users.get(call.from_user.id), settings)
        return

    if c.notifier:
        from app.admin.payouts import card_markup, card_text

        sent = await c.notifier.payout_requested(
            await card_text(c, call.from_user.id),
            await card_markup(c, call.from_user.id))
        if not sent:
            # заявка не дошла до админов — снимаем метку, чтобы человек не завис
            await c.payouts.cancel_request(call.from_user.id)
            await call.answer('Не удалось отправить заявку. Попробуйте позже.',
                              show_alert=True)
            return

    await call.answer(f'Заявка на {result.amount}₽ отправлена {e("ok")}', show_alert=True)
    await payout_menu(call, c, await c.users.get(call.from_user.id), settings,
                      note='Заявка принята. Обычно выплата занимает до суток.')


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='payouts')
    feature = Feature('features.payouts_enabled')

    router.callback_query.register(payout_menu, Menu.filter(F.screen == 'payout'), feature)
    router.callback_query.register(payout_menu, Payout.filter(F.action == 'menu'), feature)
    router.callback_query.register(pick_method, Payout.filter(F.action == 'pick'), feature)
    router.callback_query.register(confirm, Payout.filter(F.action == 'confirm'), feature)
    router.callback_query.register(order, Payout.filter(F.action == 'order'), feature)

    router.callback_query.register(methods_screen, Payout.filter(F.action == 'methods'), feature)
    router.callback_query.register(add_method, Payout.filter(F.action == 'add'), feature)
    router.callback_query.register(set_type, Payout.filter(F.action == 'type'), feature)
    router.callback_query.register(ask_field, Payout.filter(F.action == 'field'), feature)
    router.callback_query.register(clear_draft, Payout.filter(F.action == 'clear'), feature)
    router.callback_query.register(save_method, Payout.filter(F.action == 'save'), feature)
    router.callback_query.register(view_method, Payout.filter(F.action == 'view'), feature)
    router.callback_query.register(delete_method, Payout.filter(F.action == 'delete'), feature)

    router.message.register(save_field, MethodInput.value)
    return router
