"""Единая админка: один роутер, десяток хендлеров, всё остальное — данные.

Разделы «Настройки» и списочные сущности (тарифы, быстрые ответы, промокоды)
рисуются автоматически по описаниям из core/settings.py и admin/entities.py.
Новая настройка = одна строка в SCHEMA. Новая сущность = один EntityAdmin.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin import broadcast
from app.admin import commands as admin_commands
from app.admin import diag as admin_diag
from app.admin import private_servers as admin_private
from app.admin import moderation as admin_moderation
from app.admin import payouts as admin_payouts
from app.admin import trial as admin_trial
from app.admin import wipe as admin_wipe
from app.admin.entities import EntityAdmin
from app.admin.stats import build_stats_text
from app.bot.callbacks import Admin as Adm
from app.bot.middlewares.emoji import PlainEmojiMiddleware
from app.settings.schema import (GROUPS, INDEX, SCHEMA, format_value, input_hint,
                                 parse_value)
from app.content.emoji import e



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
def main_kb(c) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("settings")} Настройки бота', 'sets'))
    for entity in c.entities.values():
        kb.row(btn(entity.title, 'elist', entity.code))
    kb.row(btn(f'{e("broadcast")} Рассылка', 'broadcast'))
    kb.row(btn(f'{e("trial")} Сброс бесплатного периода', 'trial'))
    kb.row(btn(f'{e("cross")} Заблокировали бота', 'blocked'))
    kb.row(btn(f'{e("tools")} Диагностика', 'diag'))
    kb.row(btn(f'{e("ban")} Заблокированные', 'banned'))
    kb.row(btn(f'{e("clipboard")} Команды бота', 'cmds'))
    kb.row(btn(f'{e("refresh")} Обновить статистику', 'main', 'refresh'))
    return kb


async def admin_command(message: types.Message, state: FSMContext, c) -> None:
    await state.clear()
    await message.answer(await c.stats.text(), reply_markup=main_kb(c).as_markup())


async def admin_main(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                     c, settings) -> None:
    """Возврат в корень админки — то же, что /admin.

    Возврат берёт цифры из кэша, «Обновить статистику» — считает заново.
    """
    await state.clear()
    await edit(call, await c.stats.text(force=callback_data.a == 'refresh'), main_kb(c))


# ── настройки: группы ───────────────────────────────────────────────────────
async def settings_groups(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await state.clear()
    kb = InlineKeyboardBuilder()
    for group in SCHEMA:
        kb.row(btn(group.title, 'grp', group.code))
    kb.row(btn(f'{e("back")} Назад', 'main'))
    await edit(call, f'<b>{e("settings")} Настройки бота</b>\n\nВыберите раздел:', kb)


async def settings_group(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    await state.clear()
    group = GROUPS.get(callback_data.a)
    if not group:
        await call.answer('Раздел не найден', show_alert=True)
        return

    values = await settings.all()
    # Скольких человек касается настройка. Скидка «истёкшим 50%» без числа
    # получателей не говорит ничего о своей цене — можно раздать полсотни
    # процентов и десяти людям, и десяти тысячам.
    people = await c.audiences.all() if any(s.audience for s in group.items) else {}

    kb = InlineKeyboardBuilder()
    lines = [f'<b>{group.title}</b>\n']

    for setting in group.items:
        value = values.get(setting.key, setting.default)
        count = people.get(setting.audience) if setting.audience else None
        who = f' — <code>{count}</code> чел.' if count is not None else ''

        lines.append(f'• {setting.title}: <b>{format_value(setting, value)}</b>{who}')
        if setting.type == 'bool':
            mark = e('ok') if value else e('cross')
            kb.row(btn(f'{mark} {setting.title}', 'tgl', setting.key))
        else:
            label = f'{e("edit")} {setting.title}: {format_value(setting, value)}'
            if count is not None:
                label += f' ({count})'
            kb.row(btn(label, 'fld', setting.key))

    kb.row(btn(f'{e("back")} Назад', 'sets'))
    await edit(call, '\n'.join(lines), kb)


async def settings_toggle(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    if callback_data.a not in INDEX:
        await call.answer('Настройка не найдена', show_alert=True)
        return
    new_value = await settings.toggle(callback_data.a, call.from_user.id)
    await call.answer(f'Включено {e("ok")}' if new_value else f'Выключено {e("cross")}')
    group_code = next(g.code for g in SCHEMA if any(s.key == callback_data.a for s in g.items))
    await settings_group(call, Adm(act='grp', a=group_code), state, c, settings)


async def settings_field(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    setting = INDEX.get(callback_data.a)
    if not setting:
        await call.answer('Настройка не найдена', show_alert=True)
        return

    group_code = next(g.code for g in SCHEMA if any(s.key == setting.key for s in g.items))
    value = await settings.get(setting.key)

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("reset")} Сбросить к значению по умолчанию', 'rst', setting.key))
    kb.row(btn(f'{e("back")} Назад', 'grp', group_code))

    await state.set_state(AdminEdit.setting_value)
    await state.update_data(setting_key=setting.key, group_code=group_code)

    people = (f'<b>Касается:</b> <code>{await c.audiences.get(setting.audience)}</code> чел.\n'
              if setting.audience else '')

    await edit(
        call,
        f'<b>{e("edit")} {setting.title}</b>\n\n'
        f'<b>Ключ:</b> <code>{setting.key}</code>\n'
        f'<b>Сейчас:</b> {format_value(setting, value)}\n'
        f'<b>По умолчанию:</b> {format_value(setting, setting.default)}\n'
        + people
        + (f'\n<blockquote>{setting.hint}</blockquote>\n' if setting.hint else '')
        + f'\n{input_hint(setting)}',
        kb,
    )


async def settings_reset(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    if callback_data.a not in INDEX:
        await call.answer('Настройка не найдена', show_alert=True)
        return
    await settings.reset(callback_data.a, call.from_user.id)
    await call.answer('Сброшено')
    group_code = next(g.code for g in SCHEMA if any(s.key == callback_data.a for s in g.items))
    await settings_group(call, Adm(act='grp', a=group_code), state, c, settings)


async def settings_value_input(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    setting = INDEX.get(data.get('setting_key', ''))
    if not setting:
        await state.clear()
        await message.answer('Настройка не найдена, начните заново: /admin')
        return

    raw = message.html_text if setting.type == 'text' else (message.text or '')
    ok, value, error = parse_value(setting, raw)
    if not ok:
        await message.answer(f'{e("warning")}{error}')
        return

    await settings.set(setting.key, value, message.from_user.id)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("back")} К разделу', 'grp', data.get('group_code', '')))
    await message.answer(
        f'{e("ok")} <b>{setting.title}</b> → {format_value(setting, value)}',
        reply_markup=kb.as_markup(),
    )


# ── списочные сущности (тарифы / быстрые ответы / промокоды) ────────────────
def entity_or_none(c, code: str) -> EntityAdmin | None:
    return c.entities.get(code)


async def entity_list(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    await state.clear()
    entity = entity_or_none(c, callback_data.a)
    if not entity:
        await call.answer('Раздел не найден', show_alert=True)
        return

    items = await entity.list()
    kb = InlineKeyboardBuilder()
    for item in items:
        mark = ''
        if entity.toggle_field:
            mark = f'{e("ok")} ' if item.get(entity.toggle_field, True) else f'{e("cross")} '
        kb.row(btn(f'{mark}{entity.label(item)}', 'eopen', entity.code, str(item.get(entity.id_field))))

    if entity.allow_create:
        kb.row(btn(f'{e("plus")} Добавить', 'enew', entity.code))
    kb.row(btn(f'{e("back")} Назад', 'main'))

    text = f'<b>{entity.title}</b>\n\n' + (
        'Пока пусто.' if not items else 'Нажмите на элемент, чтобы изменить его.')
    await edit(call, text, kb)


async def entity_open(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    await state.clear()
    entity = entity_or_none(c, callback_data.a)
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
            mark = e('ok') if value else e('cross')
            kb.row(btn(f'{mark} {setting.title}', 'etgf',
                       f'{entity.code}|{setting.key}', callback_data.b))
        else:
            kb.row(btn(f'{e("edit")} {setting.title}', 'eedit', f'{entity.code}|{setting.key}', callback_data.b))

    if entity.toggle_field:
        active = item.get(entity.toggle_field, True)
        kb.row(btn(f'{e("cross")} Выключить' if active else f'{e("ok")} Включить', 'etgl', entity.code, callback_data.b))
    kb.row(btn(f'{e("up")}', 'eup', entity.code, callback_data.b),
           btn(f'{e("down_arrow")}', 'edn', entity.code, callback_data.b))
    if entity.allow_delete:
        kb.row(btn(f'{e("trash")} Удалить', 'edel', entity.code, callback_data.b))
    kb.row(btn(f'{e("back")} К списку', 'elist', entity.code))

    await edit(call, '\n'.join(lines), kb)


async def entity_toggle(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    entity = entity_or_none(c, callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    enabled = await entity.toggle(callback_data.b)
    await call.answer(f'Включено {e("ok")}' if enabled else f'Выключено {e("cross")}')
    await entity_open(call, callback_data, state, c, settings)


async def entity_move(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    entity = entity_or_none(c, callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    moved = await entity.move(callback_data.b, -1 if callback_data.act == 'eup' else 1)
    await call.answer('Порядок обновлён' if moved else 'Дальше некуда')
    await entity_open(call, Adm(act='eopen', a=callback_data.a, b=callback_data.b), state, c, settings)


async def entity_edit_field(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    entity_code, field_key = callback_data.a.split('|', 1)
    entity = entity_or_none(c, entity_code)
    setting = entity.field_by_key(field_key) if entity else None
    item = await entity.get(callback_data.b) if entity else None
    if not entity or not setting or not item:
        await call.answer('Не найдено', show_alert=True)
        return

    await state.set_state(AdminEdit.entity_value)
    await state.update_data(entity_code=entity_code, field_key=field_key, item_id=callback_data.b)

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("back")} Отмена', 'eopen', entity_code, callback_data.b))
    await edit(
        call,
        f'<b>{e("edit")} {setting.title}</b>\n\n'
        f'<b>Элемент:</b> <code>{callback_data.b}</code>\n'
        f'<b>Сейчас:</b> {format_value(setting, item.get(setting.key, setting.default))}\n'
        + (f'\n<blockquote>{setting.hint}</blockquote>\n' if setting.hint else '')
        + f'\n{input_hint(setting)}',
        kb,
    )


async def entity_value_input(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    entity = entity_or_none(c, data.get('entity_code', ''))
    setting = entity.field_by_key(data.get('field_key', '')) if entity else None
    if not entity or not setting:
        await state.clear()
        await message.answer('Элемент не найден, начните заново: /admin')
        return

    raw = message.html_text if setting.type == 'text' else (message.text or '')
    ok, value, error = parse_value(setting, raw)
    if not ok:
        await message.answer(f'{e("warning")}{error}')
        return

    await entity.set_field(data['item_id'], setting.key, value)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("back")} К элементу', 'eopen', entity.code, data['item_id']))
    await message.answer(
        f'{e("ok")} <b>{setting.title}</b> → {format_value(setting, value)}',
        reply_markup=kb.as_markup(),
    )


async def entity_new(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    entity = entity_or_none(c, callback_data.a)
    if not entity or not entity.allow_create:
        await call.answer('Недоступно', show_alert=True)
        return

    await state.set_state(AdminEdit.entity_new_id)
    await state.update_data(entity_code=entity.code)

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("back")} Отмена', 'elist', entity.code))
    await edit(call, f'<b>{e("plus")} {entity.title}</b>\n\n{entity.id_hint}', kb)


async def entity_new_input(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    entity = entity_or_none(c, data.get('entity_code', ''))
    if not entity:
        await state.clear()
        await message.answer('Раздел не найден, начните заново: /admin')
        return

    item_id = (message.text or '').strip()
    if not item_id or len(item_id) < 2:
        await message.answer(f'{e("warning")}Слишком короткий идентификатор.')
        return
    if await entity.get(item_id):
        await message.answer(f'{e("warning")}Такой уже существует, отправьте другой.')
        return

    await entity.create(item_id)
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("settings")} Настроить', 'eopen', entity.code, item_id))
    await message.answer(
        f'{e("ok")} Создано: <code>{item_id}</code>\nТеперь задайте поля.',
        reply_markup=kb.as_markup(),
    )


async def entity_delete_ask(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    entity = entity_or_none(c, callback_data.a)
    if not entity or not entity.allow_delete:
        await call.answer('Недоступно', show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(btn(f'{e("trash")} Да, удалить', 'edelok', entity.code, callback_data.b))
    kb.row(btn(f'{e("back")} Отмена', 'eopen', entity.code, callback_data.b))
    await edit(call, f'Удалить <code>{callback_data.b}</code> безвозвратно?', kb)


async def entity_delete(call: types.CallbackQuery, callback_data: Adm, state: FSMContext, c, settings) -> None:
    entity = entity_or_none(c, callback_data.a)
    if not entity:
        await call.answer('Не найдено', show_alert=True)
        return
    await entity.delete(callback_data.b)
    await call.answer('Удалено')
    await entity_list(call, Adm(act='elist', a=entity.code), state, c, settings)


def create_router(admin_ids) -> Router:
    """Собирает новый роутер админки.

    Именно фабрика, а не модульный синглтон: Router в aiogram можно
    подключить только к одному Dispatcher, поэтому синглтон ломает и тесты,
    и любой сценарий с двумя ботами (например, отдельный бот поддержки).
    Заодно здесь видно всю карту «событие → хендлер» одним списком.
    """
    router = Router(name='admin')
    router.message.filter(F.from_user.id.in_(set(admin_ids)))
    router.callback_query.filter(F.from_user.id.in_(set(admin_ids)))

    # Админка всегда на обычных значках: если кастомные вдруг начнут
    # отклоняться Telegram, экран с тумблером должен остаться рабочим.
    router.message.middleware(PlainEmojiMiddleware())
    router.callback_query.middleware(PlainEmojiMiddleware())

    router.message.register(admin_command, Command('admin'))
    router.callback_query.register(admin_main, Adm.filter(F.act == 'main'))
    router.callback_query.register(settings_groups, Adm.filter(F.act == 'sets'))
    router.callback_query.register(settings_group, Adm.filter(F.act == 'grp'))
    router.callback_query.register(settings_toggle, Adm.filter(F.act == 'tgl'))
    router.callback_query.register(settings_field, Adm.filter(F.act == 'fld'))
    router.callback_query.register(settings_reset, Adm.filter(F.act == 'rst'))
    router.message.register(settings_value_input, AdminEdit.setting_value)
    router.callback_query.register(entity_list, Adm.filter(F.act == 'elist'))
    router.callback_query.register(entity_open, Adm.filter(F.act == 'eopen'))
    router.callback_query.register(entity_toggle, Adm.filter(F.act == 'etgl'))
    router.callback_query.register(entity_move, Adm.filter(F.act.in_({'eup', 'edn'})))
    router.callback_query.register(entity_edit_field, Adm.filter(F.act == 'eedit'))
    router.message.register(entity_value_input, AdminEdit.entity_value)
    router.callback_query.register(entity_new, Adm.filter(F.act == 'enew'))
    router.message.register(entity_new_input, AdminEdit.entity_new_id)
    router.callback_query.register(entity_delete_ask, Adm.filter(F.act == 'edel'))
    router.callback_query.register(entity_delete, Adm.filter(F.act == 'edelok'))

    # разделы в своих файлах: панель не должна расти на каждую новую функцию
    broadcast.register(router)
    admin_payouts.register(router)
    admin_moderation.register(router)
    admin_trial.register(router)
    admin_wipe.register(router)
    admin_diag.register(router)
    admin_private.register(router)
    admin_commands.register(router)
    return router
