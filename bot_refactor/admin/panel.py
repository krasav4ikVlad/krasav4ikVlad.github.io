"""Единая админка: один роутер, десяток хендлеров, всё остальное — данные.

Разделы «Настройки» и списочные сущности (тарифы, быстрые ответы, промокоды)
рисуются автоматически по описаниям из core/settings.py и admin/entities.py.
Новая настройка = одна строка в SCHEMA. Новая сущность = один EntityAdmin.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loader import admins_ids
from core.settings import (
    GROUPS, INDEX, S, SCHEMA, format_value, input_hint, parse_value,
)
from admin.entities import ENTITIES, EntityAdmin
from admin.stats import build_stats_text

router = Router()
router.message.filter(F.from_user.id.in_(admins_ids))
router.callback_query.filter(F.from_user.id.in_(admins_ids))


class Adm(CallbackData, prefix='adm'):
    act: str
    a: str = ''
    b: str = ''


class AdminEdit(StatesGroup):
    setting_value = State()
    entity_value = State()
    entity_new_id = State()


def btn(text: str, act: str, a: str = '', b: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=Adm(act=act, a=a, b=b).pack())


async def edit(call: types.CallbackQuery, text: str, kb: InlineKeyboardBuilder) -> None:
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    try:
        # часть переходов уже ответила на callback (например «Включено ✅»),
        # повторный answer Telegram отклоняет — это не ошибка сценария
        await call.answer()
    except Exception:
        pass


# ── главное меню ────────────────────────────────────────────────────────────
def main_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(btn('⚙️ Настройки бота', 'sets'))
    for entity in ENTITIES.values():
        kb.row(btn(entity.title, 'elist', entity.code))
    kb.row(btn('💬 Рассылка', 'broadcast'))
    kb.row(btn('🔄 Обновить статистику', 'main'))
    return kb


@router.message(Command('admin'))
async def admin_command(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await build_stats_text(), reply_markup=main_kb().as_markup())


@router.callback_query(Adm.filter(F.act == 'main'))
async def admin_main(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit(call, await build_stats_text(), main_kb())


# ── настройки: группы ───────────────────────────────────────────────────────
@router.callback_query(Adm.filter(F.act == 'sets'))
async def settings_groups(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardBuilder()
    for group in SCHEMA:
        kb.row(btn(group.title, 'grp', group.code))
    kb.row(btn('⬅️ Назад', 'main'))
    await edit(call, '<b>⚙️ Настройки бота</b>\n\nВыберите раздел:', kb)


@router.callback_query(Adm.filter(F.act == 'grp'))
async def settings_group(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    await state.clear()
    group = GROUPS.get(callback_data.a)
    if not group:
        await call.answer('Раздел не найден', show_alert=True)
        return

    values = await S.all()
    kb = InlineKeyboardBuilder()
    lines = [f'<b>{group.title}</b>\n']

    for setting in group.items:
        value = values.get(setting.key, setting.default)
        lines.append(f'• {setting.title}: <b>{format_value(setting, value)}</b>')
        if setting.type == 'bool':
            kb.row(btn(f'{"✅" if value else "❌"} {setting.title}', 'tgl', setting.key))
        else:
            kb.row(btn(f'✏️ {setting.title}: {format_value(setting, value)}', 'fld', setting.key))

    kb.row(btn('⬅️ Назад', 'sets'))
    await edit(call, '\n'.join(lines), kb)


@router.callback_query(Adm.filter(F.act == 'tgl'))
async def settings_toggle(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    if callback_data.a not in INDEX:
        await call.answer('Настройка не найдена', show_alert=True)
        return
    new_value = await S.toggle(callback_data.a, call.from_user.id)
    await call.answer('Включено ✅' if new_value else 'Выключено ❌')
    group_code = next(g.code for g in SCHEMA if any(s.key == callback_data.a for s in g.items))
    await settings_group(call, Adm(act='grp', a=group_code), state)


@router.callback_query(Adm.filter(F.act == 'fld'))
async def settings_field(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    setting = INDEX.get(callback_data.a)
    if not setting:
        await call.answer('Настройка не найдена', show_alert=True)
        return

    group_code = next(g.code for g in SCHEMA if any(s.key == setting.key for s in g.items))
    value = await S.get(setting.key)

    kb = InlineKeyboardBuilder()
    kb.row(btn('♻️ Сбросить к значению по умолчанию', 'rst', setting.key))
    kb.row(btn('⬅️ Назад', 'grp', group_code))

    await state.set_state(AdminEdit.setting_value)
    await state.update_data(setting_key=setting.key, group_code=group_code)

    await edit(
        call,
        f'<b>✏️ {setting.title}</b>\n\n'
        f'<b>Ключ:</b> <code>{setting.key}</code>\n'
        f'<b>Сейчас:</b> {format_value(setting, value)}\n'
        f'<b>По умолчанию:</b> {format_value(setting, setting.default)}\n'
        + (f'\n<blockquote>{setting.hint}</blockquote>\n' if setting.hint else '')
        + f'\n{input_hint(setting)}',
        kb,
    )


@router.callback_query(Adm.filter(F.act == 'rst'))
async def settings_reset(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    if callback_data.a not in INDEX:
        await call.answer('Настройка не найдена', show_alert=True)
        return
    await S.reset(callback_data.a, call.from_user.id)
    await call.answer('Сброшено')
    group_code = next(g.code for g in SCHEMA if any(s.key == callback_data.a for s in g.items))
    await settings_group(call, Adm(act='grp', a=group_code), state)


@router.message(AdminEdit.setting_value)
async def settings_value_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    setting = INDEX.get(data.get('setting_key', ''))
    if not setting:
        await state.clear()
        await message.answer('Настройка не найдена, начните заново: /admin')
        return

    raw = message.html_text if setting.type == 'text' else (message.text or '')
    ok, value, error = parse_value(setting, raw)
    if not ok:
        await message.answer(f'❗️{error}')
        return

    await S.set(setting.key, value, message.from_user.id)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn('⬅️ К разделу', 'grp', data.get('group_code', '')))
    await message.answer(
        f'✅ <b>{setting.title}</b> → {format_value(setting, value)}',
        reply_markup=kb.as_markup(),
    )


# ── списочные сущности (тарифы / быстрые ответы / промокоды) ────────────────
def entity_or_none(code: str) -> EntityAdmin | None:
    return ENTITIES.get(code)


@router.callback_query(Adm.filter(F.act == 'elist'))
async def entity_list(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    await state.clear()
    entity = entity_or_none(callback_data.a)
    if not entity:
        await call.answer('Раздел не найден', show_alert=True)
        return

    items = await entity.list()
    kb = InlineKeyboardBuilder()
    for item in items:
        mark = ''
        if entity.toggle_field:
            mark = '✅ ' if item.get(entity.toggle_field, True) else '❌ '
        kb.row(btn(f'{mark}{entity.label(item)}', 'eopen', entity.code, str(item.get(entity.id_field))))

    if entity.allow_create:
        kb.row(btn('➕ Добавить', 'enew', entity.code))
    kb.row(btn('⬅️ Назад', 'main'))

    text = f'<b>{entity.title}</b>\n\n' + (
        'Пока пусто.' if not items else 'Нажмите на элемент, чтобы изменить его.')
    await edit(call, text, kb)


@router.callback_query(Adm.filter(F.act == 'eopen'))
async def entity_open(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    await state.clear()
    entity = entity_or_none(callback_data.a)
    item = await entity.get(callback_data.b) if entity else None
    if not entity or not item:
        await call.answer('Не найдено', show_alert=True)
        return

    lines = [f'<b>{entity.title}</b>\n', f'<b>{entity.id_field}:</b> <code>{callback_data.b}</code>']
    kb = InlineKeyboardBuilder()

    for setting in entity.fields:
        value = item.get(setting.key, setting.default)
        lines.append(f'<b>{setting.title}:</b> {format_value(setting, value)}')
        if setting.type == 'bool':
            kb.row(btn(f'{"✅" if value else "❌"} {setting.title}', 'etgf',
                       f'{entity.code}|{setting.key}', callback_data.b))
        else:
            kb.row(btn(f'✏️ {setting.title}', 'eedit', f'{entity.code}|{setting.key}', callback_data.b))

    if entity.toggle_field:
        active = item.get(entity.toggle_field, True)
        kb.row(btn('❌ Выключить' if active else '✅ Включить', 'etgl', entity.code, callback_data.b))
    kb.row(btn('⬆️', 'eup', entity.code, callback_data.b),
           btn('⬇️', 'edn', entity.code, callback_data.b))
    if entity.allow_delete:
        kb.row(btn('🗑 Удалить', 'edel', entity.code, callback_data.b))
    kb.row(btn('⬅️ К списку', 'elist', entity.code))

    await edit(call, '\n'.join(lines), kb)


@router.callback_query(Adm.filter(F.act == 'etgl'))
async def entity_toggle(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    entity = entity_or_none(callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    enabled = await entity.toggle(callback_data.b)
    await call.answer('Включено ✅' if enabled else 'Выключено ❌')
    await entity_open(call, callback_data, state)


@router.callback_query(Adm.filter(F.act.in_({'eup', 'edn'})))
async def entity_move(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    entity = entity_or_none(callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    moved = await entity.move(callback_data.b, -1 if callback_data.act == 'eup' else 1)
    await call.answer('Порядок обновлён' if moved else 'Дальше некуда')
    await entity_open(call, Adm(act='eopen', a=callback_data.a, b=callback_data.b), state)


@router.callback_query(Adm.filter(F.act == 'eedit'))
async def entity_edit_field(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    entity_code, field_key = callback_data.a.split('|', 1)
    entity = entity_or_none(entity_code)
    setting = entity.field_by_key(field_key) if entity else None
    item = await entity.get(callback_data.b) if entity else None
    if not entity or not setting or not item:
        await call.answer('Не найдено', show_alert=True)
        return

    await state.set_state(AdminEdit.entity_value)
    await state.update_data(entity_code=entity_code, field_key=field_key, item_id=callback_data.b)

    kb = InlineKeyboardBuilder()
    kb.row(btn('⬅️ Отмена', 'eopen', entity_code, callback_data.b))
    await edit(
        call,
        f'<b>✏️ {setting.title}</b>\n\n'
        f'<b>Элемент:</b> <code>{callback_data.b}</code>\n'
        f'<b>Сейчас:</b> {format_value(setting, item.get(setting.key, setting.default))}\n'
        + (f'\n<blockquote>{setting.hint}</blockquote>\n' if setting.hint else '')
        + f'\n{input_hint(setting)}',
        kb,
    )


@router.message(AdminEdit.entity_value)
async def entity_value_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    entity = entity_or_none(data.get('entity_code', ''))
    setting = entity.field_by_key(data.get('field_key', '')) if entity else None
    if not entity or not setting:
        await state.clear()
        await message.answer('Элемент не найден, начните заново: /admin')
        return

    raw = message.html_text if setting.type == 'text' else (message.text or '')
    ok, value, error = parse_value(setting, raw)
    if not ok:
        await message.answer(f'❗️{error}')
        return

    await entity.set_field(data['item_id'], setting.key, value)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn('⬅️ К элементу', 'eopen', entity.code, data['item_id']))
    await message.answer(
        f'✅ <b>{setting.title}</b> → {format_value(setting, value)}',
        reply_markup=kb.as_markup(),
    )


@router.callback_query(Adm.filter(F.act == 'enew'))
async def entity_new(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    entity = entity_or_none(callback_data.a)
    if not entity or not entity.allow_create:
        await call.answer('Недоступно', show_alert=True)
        return

    await state.set_state(AdminEdit.entity_new_id)
    await state.update_data(entity_code=entity.code)

    kb = InlineKeyboardBuilder()
    kb.row(btn('⬅️ Отмена', 'elist', entity.code))
    await edit(call, f'<b>➕ {entity.title}</b>\n\n{entity.id_hint}', kb)


@router.message(AdminEdit.entity_new_id)
async def entity_new_input(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    entity = entity_or_none(data.get('entity_code', ''))
    if not entity:
        await state.clear()
        await message.answer('Раздел не найден, начните заново: /admin')
        return

    item_id = (message.text or '').strip()
    if not item_id or len(item_id) < 2:
        await message.answer('❗️Слишком короткий идентификатор.')
        return
    if await entity.get(item_id):
        await message.answer('❗️Такой уже существует, отправьте другой.')
        return

    await entity.create(item_id)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn('⚙️ Настроить', 'eopen', entity.code, item_id))
    await message.answer(
        f'✅ Создано: <code>{item_id}</code>\nТеперь задайте поля.',
        reply_markup=kb.as_markup(),
    )


@router.callback_query(Adm.filter(F.act == 'edel'))
async def entity_delete_ask(call: types.CallbackQuery, callback_data: Adm) -> None:
    entity = entity_or_none(callback_data.a)
    if not entity or not entity.allow_delete:
        await call.answer('Недоступно', show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(btn('🗑 Да, удалить', 'edelok', entity.code, callback_data.b))
    kb.row(btn('⬅️ Отмена', 'eopen', entity.code, callback_data.b))
    await edit(call, f'Удалить <code>{callback_data.b}</code> безвозвратно?', kb)


@router.callback_query(Adm.filter(F.act == 'edelok'))
async def entity_delete(call: types.CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    entity = entity_or_none(callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    await entity.delete(callback_data.b)
    await call.answer('Удалено')
    await entity_list(call, Adm(act='elist', a=entity.code), state)
