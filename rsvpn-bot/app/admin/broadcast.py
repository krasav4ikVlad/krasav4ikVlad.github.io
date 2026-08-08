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
from app.domain.segments import AUDIENCES, SEGMENTS, audience_query
from app.content.emoji import e

log = logging.getLogger(__name__)


class Broadcast(StatesGroup):
    text = State()


# запущенные рассылки: см. комментарий в start_sending
_running: set[asyncio.Task] = set()


def _btn(text: str, act: str, a: str = '', b: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=Adm(act=act, a=a, b=b).pack())


async def _count(c, audience: str) -> int:
    # общий счётчик на всю админку: см. app/admin/audiences.py
    return await c.audiences.get(audience)


# ── выбор аудитории ─────────────────────────────────────────────────────────
async def menu(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await state.clear()

    kb = InlineKeyboardBuilder()
    for code, (title, _) in AUDIENCES.items():
        kb.row(_btn(f'{title} — {await _count(c, code)}', 'bcseg', code))
    kb.row(_btn(f'{e("target")} Один сегмент', 'bcsegs'))
    kb.row(_btn(f'{e("back")} Назад', 'main'))

    await call.message.edit_text(
        f'<b>{e("broadcast")} Рассылка</b>\n\nКому отправляем?', reply_markup=kb.as_markup())
    await call.answer()


async def segment_list(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    """Точечная аудитория: конкретный сегмент, а не группа."""
    await state.clear()

    kb = InlineKeyboardBuilder()
    for segment in SEGMENTS:
        # код сегмента едет в отдельном поле: двоеточие — разделитель
        # callback_data у aiogram, и «one:new_trial_d0» роняет pack()
        kb.row(_btn(segment.title, 'bcseg', segment.code, 'one'))
    kb.row(_btn(f'{e("back")} Назад', 'broadcast'))

    await call.message.edit_text(f'<b>{e("broadcast")} Рассылка</b>\n\nВыберите сегмент:',
                                 reply_markup=kb.as_markup())
    await call.answer()


# ── текст ───────────────────────────────────────────────────────────────────
async def ask_text(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                   c, settings) -> None:
    audience, one = callback_data.a, callback_data.b == 'one'
    total = (await c.users.col.count_documents({'growth.segment': audience})
             if one else await _count(c, audience))

    await state.set_state(Broadcast.text)
    await state.update_data(audience=audience, one=one, total=total)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'broadcast'))
    await call.message.edit_text(
        f'<b>{e("broadcast")} Рассылка</b>\n\nПолучателей: <code>{total}</code>\n\n'
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
    kb.row(_btn(f'{e("ok")} Отправить', 'bcgo'), _btn(f'{e("back")} Отмена', 'broadcast'))

    # Показываем именно тем же способом, каким уйдёт: если разметка битая,
    # ошибка вылезет здесь, а не на десяти тысячах получателей
    try:
        await message.answer(text)
    except Exception as exc:
        await message.answer(f'{e("warning")} Разметка сломана, Telegram отказался её принять:\n'
                             f'<code>{exc}</code>\n\nПоправьте и пришлите заново.')
        return

    await message.answer(
        f'{e("up_finger")} Так увидят получатели.\n\nПолучателей: <code>{data.get("total", 0)}</code>',
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
    query = ({'growth.segment': audience} if data.get('one') else audience_query(audience))

    await call.message.edit_text(
        f'<b>{e("broadcast")} Рассылка запущена</b>\n\nПолучателей: <code>{data.get("total", 0)}</code>\n'
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
        await message.answer(f'<b>{e("ok")} Рассылка завершена</b>\n\n'
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
