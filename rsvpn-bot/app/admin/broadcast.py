"""Ручные рассылки.

Кнопка «💬 Рассылка» в админке была, а обработчика к ней не было — нажатие
ничего не делало. Здесь она оживает.

От старого admin.py отличается тремя вещами, каждая из которых там была
источником проблем:

* аудитория берётся из `growth.segment`, а не собирается перебором всей базы
  с ручными `if` по датам — те же сегменты, что и у автокампаний;
* отправка идёт через campaigns/sender.py: паузы, флуд-лимит Telegram и учёт
  заблокировавших. Старая рассылка слала подряд без пауз и теряла людей
  в `except: failed += 1`;
* перед отправкой обязателен предпросмотр — админ видит ровно то сообщение,
  которое уйдёт, и подтверждает отправку числом получателей.

Рассылка идёт фоновой задачей: при 10 000 получателей и паузе 40 мс это
около семи минут, и всё это время админка должна оставаться отзывчивой.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.campaigns.sender import Sender
from app.domain.segments import BY_GROUP, SEGMENTS

log = logging.getLogger(__name__)

# Готовые наборы сегментов: то, что в старой админке было кнопками
# «Всем / активным / истёкшим / без подписки»
AUDIENCES: dict[str, tuple[str, tuple[str, ...]]] = {
    'all': ('📢 Всем', ()),
    'trial': ('🧪 На триале', BY_GROUP.get('trial', ())),
    'active': ('🟢 С активной подпиской', BY_GROUP.get('active', ())),
    'expired': ('🔁 Истёкшие', BY_GROUP.get('expired', ())),
    'churned': ('💀 Давно ушедшие', BY_GROUP.get('churned', ())),
    'no_sub': ('⚪ Без подписки и оплат', ('inactive_no_sub',)),
}


class Broadcast(StatesGroup):
    text = State()


# запущенные рассылки: см. комментарий в start_sending
_running: set[asyncio.Task] = set()


def _btn(text: str, act: str, a: str = '', b: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=Adm(act=act, a=a, b=b).pack())


def _query(audience: str) -> dict:
    """Фильтр по сегментам. Пустой набор = вся база."""
    codes = AUDIENCES.get(audience, ('', ()))[1]
    return {'growth.segment': {'$in': list(codes)}} if codes else {}


async def _count(c, audience: str) -> int:
    return await c.users.col.count_documents(_query(audience))


# ── выбор аудитории ─────────────────────────────────────────────────────────
async def menu(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await state.clear()

    kb = InlineKeyboardBuilder()
    for code, (title, _) in AUDIENCES.items():
        kb.row(_btn(f'{title} — {await _count(c, code)}', 'bcseg', code))
    kb.row(_btn('🎯 Один сегмент', 'bcsegs'))
    kb.row(_btn('⬅️ Назад', 'main'))

    await call.message.edit_text(
        '<b>💬 Рассылка</b>\n\nКому отправляем?', reply_markup=kb.as_markup())
    await call.answer()


async def segment_list(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    """Точечная аудитория: конкретный сегмент, а не группа."""
    await state.clear()

    kb = InlineKeyboardBuilder()
    for segment in SEGMENTS:
        kb.row(_btn(segment.title, 'bcseg', f'one:{segment.code}'))
    kb.row(_btn('⬅️ Назад', 'broadcast'))

    await call.message.edit_text('<b>💬 Рассылка</b>\n\nВыберите сегмент:',
                                 reply_markup=kb.as_markup())
    await call.answer()


# ── текст ───────────────────────────────────────────────────────────────────
async def ask_text(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                   c, settings) -> None:
    audience = callback_data.a
    total = (await c.users.col.count_documents({'growth.segment': audience.split(':', 1)[1]})
             if audience.startswith('one:') else await _count(c, audience))

    await state.set_state(Broadcast.text)
    await state.update_data(audience=audience, total=total)

    kb = InlineKeyboardBuilder()
    kb.row(_btn('⬅️ Отмена', 'broadcast'))
    await call.message.edit_text(
        f'<b>💬 Рассылка</b>\n\nПолучателей: <code>{total}</code>\n\n'
        'Отправьте текст сообщения. Работает HTML-разметка: '
        '<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a href&gt;</code>.',
        reply_markup=kb.as_markup())
    await call.answer()


async def preview(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    text = message.html_text or ''
    if not text.strip():
        await message.answer('Пустое сообщение отправить нельзя.')
        return

    await state.update_data(text=text)

    kb = InlineKeyboardBuilder()
    kb.row(_btn('✅ Отправить', 'bcgo'), _btn('⬅️ Отмена', 'broadcast'))

    # Показываем именно тем же способом, каким уйдёт: если разметка битая,
    # ошибка вылезет здесь, а не на десяти тысячах получателей
    try:
        await message.answer(text)
    except Exception as exc:
        await message.answer(f'❗️ Разметка сломана, Telegram отказался её принять:\n'
                             f'<code>{exc}</code>\n\nПоправьте и пришлите заново.')
        return

    await message.answer(
        f'☝️ Так увидят получатели.\n\nПолучателей: <code>{data.get("total", 0)}</code>',
        reply_markup=kb.as_markup())


# ── отправка ────────────────────────────────────────────────────────────────
async def start_sending(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    text = data.get('text')
    if not text:
        await call.answer('Текст потерялся, начните заново', show_alert=True)
        return

    await state.clear()
    audience = data.get('audience', 'all')
    query = ({'growth.segment': audience.split(':', 1)[1]}
             if audience.startswith('one:') else _query(audience))

    await call.message.edit_text(
        f'<b>💬 Рассылка запущена</b>\n\nПолучателей: <code>{data.get("total", 0)}</code>\n'
        'Отчёт придёт сюда же, когда закончится.', reply_markup=None)
    await call.answer('Пошла рассылка')

    # фоном: иначе Telegram оборвёт обработчик по таймауту на большой базе.
    # Ссылку держим сами — задачу без владельца сборщик мусора может убить
    # прямо посреди рассылки.
    task = asyncio.create_task(_run(c, call.bot, call.message, query, text))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def _run(c, bot, message, query: dict, text: str) -> None:
    delay = await c.settings.int('campaign.broadcast_delay_ms') / 1000
    sender = Sender()
    sent = failed = 0

    async for user in c.users.col.find(query, {'user_data.user_id': 1}):
        user_id = ((user or {}).get('user_data') or {}).get('user_id')
        if not user_id:
            continue
        if await sender.send(bot, user_id, text):
            sent += 1
        else:
            failed += 1
        if delay:
            await asyncio.sleep(delay)

    log.info('рассылка завершена: отправлено %s, не доставлено %s', sent, failed)
    try:
        await message.answer(f'<b>✅ Рассылка завершена</b>\n\n'
                             f'Доставлено: <code>{sent}</code>\n'
                             f'Не доставлено: <code>{failed}</code>')
    except Exception as exc:
        log.warning('отчёт о рассылке не отправлен: %s', exc)


def register(router: Router) -> None:
    """Подключается к админскому роутеру: фильтр «только админ» уже стоит там."""
    router.callback_query.register(menu, Adm.filter(F.act == 'broadcast'))
    router.callback_query.register(segment_list, Adm.filter(F.act == 'bcsegs'))
    router.callback_query.register(ask_text, Adm.filter(F.act == 'bcseg'))
    router.callback_query.register(start_sending, Adm.filter(F.act == 'bcgo'))
    router.message.register(preview, Broadcast.text)
