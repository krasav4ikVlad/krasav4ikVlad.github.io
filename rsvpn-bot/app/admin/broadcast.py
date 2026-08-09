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
from app.core import db as names
from datetime import timedelta

from app.core.time import now
from app.domain.segments import AUDIENCES, SEGMENTS, audience_query
from app.content.emoji import e

log = logging.getLogger(__name__)


class Broadcast(StatesGroup):
    text = State()


# запущенные рассылки: см. комментарий в start_sending
_running: dict[str, asyncio.Task] = {}

# Сколько писем уходит одной пачкой. Последовательная отправка упиралась не
# в паузу, а в round-trip до Telegram: 150–300 мс на письмо, то есть 4–6
# писем в секунду вместо расчётных 25. На 190 тысячах это разница между
# двумя часами и половиной суток — и всё это время рассылка выглядит
# зависшей, потому что счётчик обновляется раз в сотню писем.
CHUNK = 25

# Сколько ждать ответа Telegram на одно письмо. Без потолка зависший запрос
# останавливает рассылку навсегда: задача жива, счётчик не двигается, в логе
# пусто. Именно так «рассылка оборвалась» и выглядит со стороны.
SEND_TIMEOUT_SEC = 60

# Через сколько секунд молчания считаем идущую рассылку мёртвой и
# перезапускаем её сами.
STALE_SEC = 180

# Предохранитель от вечного цикла: если рассылка падает сразу после старта,
# сторож поднимет её ограниченное число раз и остановится.
MAX_RESTARTS = 200


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
    query = {'growth.segment': audience} if one else audience_query(audience)
    total = (await c.users.col.count_documents(query)
             if one else await _count(c, audience))
    blocked = await c.users.blocked_count(query)

    await state.set_state(Broadcast.text)
    await state.update_data(audience=audience, one=one, total=total - blocked)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'broadcast'))
    await call.message.edit_text(
        f'<b>{e("broadcast")} Рассылка</b>\n\n'
        f'Получателей: <code>{total - blocked}</code>\n'
        + (f'Заблокировали бота: <code>{blocked}</code> — им не пойдёт\n'
           if blocked else '')
        + '\n'
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
#
# Рассылка переживала не всё. Два места ломали её молча:
#
# * задача запускалась через create_task, и её исключение никто не читал.
#   Любой сбой означал «счётчик замер на середине», пустой лог и никакого
#   ответа на вопрос «почему»;
# * получатели брались курсором Mongo, открытым на всё время отправки.
#   Сервер закрывает курсор, простоявший дольше десяти минут, а десять
#   тысяч писем по 40 мс — это семь минут плюс паузы флуд-контроля. Дальше
#   getMore падал с CursorNotFound посреди цикла.
#
# Теперь список получателей читается целиком заранее (десять тысяч id —
# меньше мегабайта), состояние лежит в базе, а падение видно и в логе, и
# в самом сообщении, и в админ-чате. Прерванную рассылку можно продолжить
# с того места, где она остановилась.

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
        f'<b>{e("broadcast")} Собираю получателей…</b>', reply_markup=None)
    await call.answer('Пошла рассылка')

    # Список — заранее и целиком: курсор, открытый на всю отправку, сервер
    # закроет по таймауту, и рассылка оборвётся на середине.
    recipients = await _recipients(c, query)
    job = {
        '_id': f'{call.from_user.id}-{int(now().timestamp())}',
        'audience': audience, 'text': text, 'recipients': recipients,
        'position': 0, 'sent': 0, 'failed': 0, 'status': 'running',
        'chat_id': call.message.chat.id, 'message_id': call.message.message_id,
        'admin_id': call.from_user.id, 'started_at': now(), 'updated_at': now(),
        'restarts': 0,
    }
    await c.db[names.BROADCASTS].insert_one(job)
    log.info('рассылка %s: получателей %s', job['_id'], len(recipients))

    _spawn(c, call.bot, job['_id'])


async def _recipients(c, query: dict) -> list[int]:
    """Кому реально пойдёт письмо: без тех, кто заблокировал бота.

    Их отсев именно здесь, а не в подсчёте аудитории: аудитория — это
    «сколько таких людей есть», а получатели — «кому дойдёт». Числа
    разойдутся, и это честно; сколько именно отсеяно, видно на экране.
    """
    docs = await c.users.col.find({**query, c.users.BLOCKED: {'$ne': True}},
                                  {'user_data.user_id': 1}).to_list(length=None)
    return [uid for doc in docs
            if (uid := ((doc or {}).get('user_data') or {}).get('user_id'))]


def _spawn(c, bot, job_id: str) -> None:
    """Запуск фоном. Ссылку держим сами — задачу без владельца сборщик
    мусора может убить прямо посреди рассылки.

    Повторный запуск той же рассылки не создаёт вторую задачу: сторож
    (`resume_stalled`) вызывает нас вслепую, и без этой проверки одна
    рассылка ушла бы получателям дважды.
    """
    alive = _running.get(job_id)
    if alive and not alive.done():
        return

    task = asyncio.create_task(_run(c, bot, job_id))
    _running[job_id] = task
    task.add_done_callback(lambda t: _running.pop(job_id, None))
    task.add_done_callback(lambda t: _log_failure(job_id, t))


def _log_failure(job_id: str, task: asyncio.Task) -> None:
    """Прочитать исключение задачи. Без этого оно оседает внутри Task, и
    оборванная рассылка выглядит как зависшая: ни строки в логе."""
    if task.cancelled():
        log.warning('рассылка %s отменена', job_id)
        return
    exc = task.exception()
    if exc:
        log.error('рассылка %s упала: %r', job_id, exc, exc_info=exc)


def eta(left: int, delay: float) -> str:
    """Сколько ещё идти. На 190 тысячах писем это часы, и знать это надо
    заранее, а не гадать, зависла рассылка или просто длинная."""
    seconds = int(left * max(delay, 0.03))
    if seconds < 90:
        return f'{seconds} сек'
    if seconds < 5400:
        return f'{seconds // 60} мин'
    return f'{seconds / 3600:.1f} ч'


def progress_text(sent: int, failed: int, total: int, done: bool = False,
                  error: str = '', pause_until: str = '', delay: float = 0.04,
                  stamp: bool = True) -> str:
    """Счётчики рассылки. Один текст на ход, на конец и на обрыв — чтобы
    цифры на экране не меняли формат в момент завершения."""
    done_count = sent + failed
    if error:
        head = f'<b>{e("attention")} Рассылка прервана</b>'
    elif done:
        head = f'<b>{e("ok")} Рассылка завершена</b>'
    elif pause_until:
        head = f'<b>{e("hourglass")} Пауза по требованию Telegram</b>'
    else:
        head = f'<b>{e("broadcast")} Рассылка идёт</b>'

    lines = [head, '']
    lines.append(f'<b>Доставлено:</b> <code>{sent}</code>')
    lines.append(f'<b>Не доставлено:</b> <code>{failed}</code>')

    if total and not done:
        left = max(0, total - done_count)
        lines.append(f'<b>Осталось:</b> <code>{left}</code> из <code>{total}</code>')
        lines.append(f'<b>Примерно ещё:</b> {eta(left, delay)}')
    elif not done:
        lines.append(f'<b>Обработано:</b> <code>{done_count}</code>')

    if pause_until:
        lines.append(f'\nTelegram просит подождать до <b>{pause_until}</b>. '
                     'Это не сбой: на большой рассылке он притормаживает '
                     'отправку. Продолжится само.')
    if error:
        lines.append(f'\n<code>{error}</code>')
        lines.append('Можно продолжить с этого места кнопкой ниже.')

    # Время последнего обновления. Без него «зависла» и «идёт медленно»
    # выглядят одинаково, и понять это можно только сидя и глядя в экран.
    if stamp and not done:
        lines.append(f'\n<i>обновлено в {now().strftime("%H:%M:%S")}</i>')
    return '\n'.join(lines)


def _resume_kb(job_id: str) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("renew")} Продолжить рассылку', 'bcres', job_id))
    return kb.as_markup()


def _running_kb(job_id: str) -> types.InlineKeyboardMarkup:
    """Кнопка остановки. На 190 тысячах писем рассылка идёт часами, и без
    неё единственный способ её прекратить — перезапуск процесса."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("cross")} Остановить', 'bcstop', job_id))
    return kb.as_markup()


async def _show(bot, job: dict, text: str, markup=None) -> None:
    """Обновить сообщение со счётчиками. Отказ Telegram не должен ломать
    рассылку: она уже идёт, и её цель — доставить письма, а не отчитаться.

    Самый частый отказ — «message is not modified»: за сотню сообщений
    ни одно не дошло и цифры не изменились. Это не ошибка.
    """
    try:
        await bot.edit_message_text(text, chat_id=job['chat_id'],
                                    message_id=job['message_id'],
                                    reply_markup=markup)
    except Exception as exc:
        log.debug('счётчик рассылки не обновлён: %s', exc)


async def _save(c, job_id: str, **fields) -> None:
    await c.db[names.BROADCASTS].update_one({'_id': job_id}, {'$set': fields})


async def _run(c, bot, job_id: str) -> None:
    job = await c.db[names.BROADCASTS].find_one({'_id': job_id})
    if not job:
        log.error('рассылка %s не найдена', job_id)
        return

    delay = await c.settings.int('campaign.broadcast_delay_ms') / 1000
    step = await c.settings.int('campaign.broadcast_progress_step') or 0
    recipients = job.get('recipients') or []
    total = len(recipients)
    position = int(job.get('position', 0) or 0)
    sent = int(job.get('sent', 0) or 0)
    failed = int(job.get('failed', 0) or 0)
    text = job.get('text') or ''
    await _save(c, job_id, status='running', updated_at=now())

    async def on_flood(seconds: int) -> None:
        """Пауза Telegram — показать её сразу, не дожидаясь конца шага.

        Иначе счётчик замирает на середине, и полчаса ожидания выглядят
        как зависшая рассылка. Именно так это и выглядело.
        """
        until = (now() + timedelta(seconds=seconds)).strftime('%H:%M:%S')
        await _show(bot, job, progress_text(sent, failed, total,
                                            pause_until=until, delay=delay),
                    _running_kb(job_id))

    sender = Sender(on_blocked=c.users.mark_blocked, on_flood=on_flood)

    async def send_one(user_id: int) -> bool:
        """Одно письмо, но с потолком по времени.

        Зависший HTTP-запрос без таймаута останавливает рассылку навсегда:
        задача жива, исключения нет, счётчик стоит. Снаружи это неотличимо
        от обрыва — и именно так рассылка «пропадала».
        """
        try:
            return await asyncio.wait_for(sender.send(bot, user_id, text),
                                          timeout=SEND_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log.warning('таймаут отправки %s', user_id)
            return False

    last_shown = position
    try:
        while position < total:
            # Пачкой, а не по одному: round-trip до Telegram (150–300 мс)
            # перекрывается, и скорость определяется паузой, а не сетью.
            chunk = recipients[position:position + CHUNK]
            results = await asyncio.gather(*(send_one(uid) for uid in chunk))
            position += len(chunk)
            sent += sum(1 for ok in results if ok)
            failed += sum(1 for ok in results if not ok)

            # Позиция и пульс — после каждой пачки, независимо от того,
            # показываем мы счётчик или нет. По updated_at сторож понимает,
            # что задача жива; по position продолжает с места обрыва.
            await _save(c, job_id, position=position, sent=sent,
                        failed=failed, updated_at=now())

            # Остановку читаем из базы, а не из памяти: кнопку нажимают
            # в другом процессе-обработчике, и общего состояния у них нет.
            fresh = await c.db[names.BROADCASTS].find_one({'_id': job_id}, {'status': 1})
            if (fresh or {}).get('status') == 'stopping':
                # 'stopped' — терминальный статус: сторож поднимает
                # оборвавшиеся рассылки сам, и остановленную руками
                # он поднимать не должен.
                await _save(c, job_id, status='stopped',
                            error='остановлена вручную', finished_at=now())
                await _show(bot, job, progress_text(
                    sent, failed, total, error='остановлена вручную',
                    delay=delay), _resume_kb(job_id))
                log.info('рассылка %s остановлена на %s из %s', job_id, position, total)
                return

            # Правка сообщения — тоже запрос к Telegram, и у него свой лимит
            # (около одного в секунду на чат). Шаг в сотню при паузе 40 мс
            # даёт обновление раз в четыре секунды — далеко от лимита.
            if step and position - last_shown >= step:
                last_shown = position
                await _show(bot, job, progress_text(sent, failed, total, delay=delay),
                            _running_kb(job_id))

            if delay:
                await asyncio.sleep(delay * len(chunk))
    except Exception as exc:
        await _save(c, job_id, position=position, sent=sent, failed=failed,
                    status='failed', error=repr(exc), finished_at=now())
        await _show(bot, job, progress_text(sent, failed, total, error=repr(exc),
                                            delay=delay),
                    _resume_kb(job_id))
        if c.notifier:
            await c.notifier.send('campaigns',
                                  f'{e("attention")} Рассылка прервана на '
                                  f'{sent + failed} из {total}: <code>{exc!r}</code>')
        raise

    await _save(c, job_id, position=position, sent=sent, failed=failed,
                status='done', finished_at=now())
    log.info('рассылка %s завершена: отправлено %s, не доставлено %s',
             job_id, sent, failed)
    await _show(bot, job, progress_text(sent, failed, total, done=True, delay=delay))


async def stop(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    """Пометить рассылку к остановке. Сама она остановится на ближайшем шаге."""
    result = await c.db[names.BROADCASTS].update_one(
        {'_id': callback_data.a, 'status': 'running'},
        {'$set': {'status': 'stopping'}})
    if not getattr(result, 'modified_count', 0):
        await call.answer('Эта рассылка уже не идёт', show_alert=True)
        return
    await call.answer('Останавливаю, подождите несколько секунд')


async def resume(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    """Продолжить прерванную рассылку с сохранённой позиции."""
    job = await c.db[names.BROADCASTS].find_one({'_id': callback_data.a})
    if not job:
        await call.answer('Рассылка не найдена', show_alert=True)
        return
    if job.get('status') == 'running':
        await call.answer('Эта рассылка и так идёт', show_alert=True)
        return
    if int(job.get('position', 0)) >= len(job.get('recipients') or []):
        await call.answer('Все получатели уже обработаны', show_alert=True)
        return

    # сообщение могло уехать далеко вверх — продолжаем в свежем
    fresh = await call.message.answer(progress_text(
        int(job.get('sent', 0)), int(job.get('failed', 0)),
        len(job.get('recipients') or [])))
    await _save(c, job['_id'], status='running',
                chat_id=fresh.chat.id, message_id=fresh.message_id)

    _spawn(c, call.bot, job['_id'])
    await call.answer('Продолжаю')


async def mark_interrupted(c) -> list[dict]:
    """Рассылки, которые шли в момент остановки процесса.

    Задача живёт в памяти, поэтому перезапуск бота (в том числе обычный
    деплой) обрывает её без следов. Отметку ставим при старте: иначе такая
    рассылка навсегда осталась бы «идущей». Дальше её поднимет сторож.
    """
    try:
        jobs = await c.db[names.BROADCASTS].find(
            {'status': {'$in': ['running', 'stopping']}}).to_list(length=None)
    except Exception as exc:
        log.warning('прерванные рассылки не проверены: %s', exc)
        return []

    revived = []
    for job in jobs:
        # «Остановить» нажали перед самым перезапуском — воля админа важнее
        # автоматики, такую рассылку сторож поднимать не должен.
        if job.get('status') == 'stopping':
            await _save(c, job['_id'], status='stopped', error='остановлена вручную')
            continue
        await _save(c, job['_id'], status='interrupted', error='процесс перезапущен')
        log.warning('рассылка %s оборвана перезапуском на %s из %s',
                    job['_id'], job.get('position', 0), len(job.get('recipients') or []))
        revived.append(job)
    return revived


# ── сторож ──────────────────────────────────────────────────────────────────
#
# Рассылка на 190 тысяч писем идёт часами, и за это время с ней случается
# всё: деплой, перезапуск по памяти, разрыв соединения с Telegram, сбой
# Mongo. Кнопка «Продолжить» это лечит, но только если кто-то смотрит на
# экран — а рассылку запускают и уходят.
#
# Поэтому продолжение не должно быть ручным действием. Раз в минуту сторож
# смотрит, нет ли рассылки, которая обязана идти, но молчит, и поднимает её
# сам — с сохранённой позиции. Руками остановленную (status='stopped') и
# завершённую не трогает.

async def resume_stalled(c, bot) -> int:
    """Поднять рассылки, которые должны идти, но не идут. Возвращает сколько."""
    border = now() - timedelta(seconds=STALE_SEC)
    try:
        jobs = await c.db[names.BROADCASTS].find(
            {'$or': [
                {'status': {'$in': ['interrupted', 'failed']}},
                # «идёт», но пульса не было дольше STALE_SEC: задача либо
                # умерла, либо висит на запросе, который никогда не вернётся
                {'status': 'running', 'updated_at': {'$lt': border}},
            ]},
            {'recipients': 0},   # список получателей тут не нужен, он тяжёлый
        ).to_list(length=None)
    except Exception as exc:
        log.warning('сторож рассылок не отработал: %s', exc)
        return 0

    revived = 0
    for job in jobs:
        job_id = job['_id']
        alive = _running.get(job_id)
        if alive and not alive.done():
            continue                       # эта живёт в текущем процессе
        if int(job.get('restarts', 0) or 0) >= MAX_RESTARTS:
            log.error('рассылка %s перезапускалась %s раз — больше не поднимаю',
                      job_id, job.get('restarts'))
            await _save(c, job_id, status='stopped',
                        error='слишком много перезапусков')
            continue

        await c.db[names.BROADCASTS].update_one(
            {'_id': job_id}, {'$inc': {'restarts': 1},
                              '$set': {'status': 'running', 'updated_at': now()}})
        log.warning('сторож поднимает рассылку %s с позиции %s (было: %s)',
                    job_id, job.get('position', 0), job.get('status'))
        _spawn(c, bot, job_id)
        revived += 1

    return revived


# ── список заблокировавших ──────────────────────────────────────────────────
#
# Telegram не сообщает о разблокировке и не отвечает на вопрос «можно ли
# писать этому человеку». Единственный способ узнать — попробовать
# отправить. Поэтому отметка не снимается сама, а очищается руками: перед
# большой акцией имеет смысл сбросить её и дать всем ещё один шанс.

async def blocked_menu(call: types.CallbackQuery, c, settings) -> None:
    total = await c.users.blocked_count()

    kb = InlineKeyboardBuilder()
    if total:
        kb.row(_btn(f'{e("broom")} Очистить список ({total})', 'bcunbl'))
    kb.row(_btn(f'{e("back")} Назад', 'main'))

    await call.message.edit_text(
        f'<b>{e("cross")} Заблокировали бота</b>\n\n'
        f'Таких сейчас: <code>{total}</code>. Рассылки им не отправляются.\n\n'
        '<blockquote>Отметка ставится сама, когда Telegram отвечает отказом '
        'на отправку. Снять её автоматически нельзя: о разблокировке Telegram '
        'не сообщает, и узнать это можно только попыткой отправить. '
        'Очистите список перед большой акцией — вернувшиеся получат письмо, '
        'а те, кто не вернулся, снова пометятся при первой же рассылке.</blockquote>',
        reply_markup=kb.as_markup())
    await call.answer()


async def unblock_all(call: types.CallbackQuery, c, settings) -> None:
    cleared = await c.users.unmark_blocked()
    log.info('админ %s очистил список заблокировавших: %s', call.from_user.id, cleared)
    await call.answer(f'Очищено: {cleared}')
    await blocked_menu(call, c, settings)


def register(router: Router) -> None:
    """Подключается к админскому роутеру: фильтр «только админ» уже стоит там."""
    router.callback_query.register(menu, Adm.filter(F.act == 'broadcast'))
    router.callback_query.register(segment_list, Adm.filter(F.act == 'bcsegs'))
    router.callback_query.register(ask_text, Adm.filter(F.act == 'bcseg'))
    router.callback_query.register(start_sending, Adm.filter(F.act == 'bcgo'))
    router.callback_query.register(resume, Adm.filter(F.act == 'bcres'))
    router.callback_query.register(stop, Adm.filter(F.act == 'bcstop'))
    router.callback_query.register(blocked_menu, Adm.filter(F.act == 'blocked'))
    router.callback_query.register(unblock_all, Adm.filter(F.act == 'bcunbl'))
    router.message.register(preview, Broadcast.text)
