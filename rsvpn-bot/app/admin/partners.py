"""Партнёры: именная ссылка, свой чат уведомлений, свои правила триала.

Метки приглашения (`/reftag`) были и раньше, но партнёр — это не только
метка. С каждым новым партнёром выясняется одно и то же: его аудитория
пришла с его площадки, и подписываться на наш канал ради трёх пробных
дней не станет — она просто уйдёт на первом же шаге. А события по его
людям нужно видеть отдельно, а не вылавливать из общей ленты, где их
перекрывают полторы тысячи чужих регистраций в день.

Поэтому у метки появились настройки, и они здесь: экран на партнёра,
кнопки — переключатели. Команды `/reftag` и `/reftags` никуда не делись,
они про то же самое; этот раздел — то же самое кнопками и со всем, чего
в командах нет.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content import ids
from app.content.emoji import e
from app.core.time import fmt, parse_dt
from app.core.time import now as time_now
from app.domain import ref_tags as domain
from app.services import partner_stats

log = logging.getLogger(__name__)


class Partner(StatesGroup):
    new = State()
    chat = State()
    pay_topic = State()
    boost = State()


async def legacy_aliases(settings) -> list[tuple[str, str]]:
    """Именные ссылки из настройки link.ref_aliases — наследство старого бота.

    Они работают (см. resolve_referrer), но счётчика и настроек у них нет:
    это просто «имя → id» строкой. Показываем, чтобы про них помнили.
    """
    raw = str(await settings.get('link.ref_aliases') or '')
    found = []
    for pair in raw.split(','):
        if ':' not in pair:
            continue
        name, owner = (part.strip() for part in pair.split(':', 1))
        if name and owner.isdigit():
            found.append((name, owner))
    return found


def _btn(text: str, act: str, a: str = '', b: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text,
                                      callback_data=Adm(act=act, a=a, b=b).pack())


# ── список ──────────────────────────────────────────────────────────────────
async def screen(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    from app.admin.panel import edit

    await state.clear()
    rows = await c.ref_tags.all(limit=50)

    lines = [f'<b>{e("friends")} Партнёры</b>', '']
    if not rows:
        lines.append('Пока ни одного.')
    else:
        total = sum(int(row.get('registrations') or 0) for row in rows)
        lines.append(f'Всего приведено: <b>{total}</b> чел.')
        lines.append('')

    kb = InlineKeyboardBuilder()
    for row in rows:
        marks = ''.join((
            e('trial') if row.get('no_channel') else '',
            e('bell') if row.get('chat_id') else '',
        ))
        kb.row(_btn(f'{row.get("tag")} — {int(row.get("registrations") or 0)} чел. {marks}',
                    'pshow', str(row.get('tag'))))

    kb.row(_btn(f'{e("plus")} Добавить партнёра', 'pnew'))
    kb.row(_btn(f'{e("back")} Назад', 'main'))

    old = await legacy_aliases(settings)
    if old:
        # Алиасы из настроек работают, но живут мимо этого экрана: ни
        # счётчика, ни настроек у них нет. Молчать о них — значит однажды
        # искать «а куда делась старая ссылка».
        lines.append('')
        lines.append(f'{e("warning")} <b>Старые ссылки из настроек</b> '
                     f'(без счётчика): ' + ', '.join(
                         f'<code>{name}</code> → <code>{ids.show(owner)}</code>'
                         for name, owner in old[:10]))
        lines.append('   <i>Заведите их метками — тогда будет видно, '
                     'сколько каждая привела.</i>')

    lines.append(f'<blockquote>{e("trial")} — триал без подписки на канал, '
                 f'{e("bell")} — свой чат уведомлений.\n\n'
                 f'Партнёр — это именная ссылка, привязанная к человеку: '
                 f'начисления по ней считаются ему, как по обычной '
                 f'реферальной.</blockquote>')
    await edit(call, '\n'.join(lines), kb)


# ── карточка партнёра ───────────────────────────────────────────────────────
async def card(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                c, settings) -> None:
    from app.admin.panel import edit

    await state.clear()
    tag = domain.normalize(callback_data.a)
    partner = await c.ref_tags.get(tag)
    if not partner:
        await call.answer('Метка не найдена', show_alert=True)
        return

    bot_username = await settings.get('link.bot_username')
    owner_id = int(partner.get('user_id') or 0)
    owner = await c.users.get(owner_id, {'user_data.username': 1})
    username = c.users.pick(owner or {}, 'user_data.username')

    lines = [f'<b>{e("friends")} Партнёр {tag}</b>', '',
             f'{e("link")} <code>{domain.link(bot_username, tag)}</code>',
             f'{e("user")} Начисления: <code>{ids.show(owner_id)}</code>'
             + (f' @{username}' if username else ''),
             f'{e("referrals")} Привёл: <b>{int(partner.get("registrations") or 0)}</b> чел.']
    if partner.get('last_at'):
        lines.append(f'{e("clock")} Последний переход: {fmt(partner["last_at"])}')
    if partner.get('note'):
        lines.append(f'{e("note")} {partner["note"]}')
    lines.append('')

    boost = domain.boost_rate(partner, time_now())
    if boost:
        left = (parse_dt(partner.get('boost_until')) - time_now()).days
        lines.append(f'{e("hot")} Повышенный процент: '
                     f'<b>{round(boost * 100)}%</b> до '
                     f'{fmt(partner.get("boost_until"), "%d.%m.%Y")} '
                     f'(осталось {max(0, left)} дн.)')
        lines.append(f'   <i>со всех его рефералов, включая старых</i>')
    lines.append(f'{e("trial")} Триал без подписки на канал: '
                 f'<b>{"да" if partner.get("no_channel") else "нет"}</b>')
    chat_id = int(partner.get('chat_id') or 0)
    if chat_id:
        topic = int(partner.get('topic_id') or 0)
        pay = int(partner.get('topic_pay_id') or 0)
        lines.append(f'{e("bell")} Чат уведомлений: <code>{chat_id}</code>')
        lines.append(f'   {e("user")} Регистрации: '
                     + (f'тема <code>{topic}</code>' if topic
                        else '<b>общая лента чата</b>'))
        lines.append(f'   {e("money")} Оплаты: '
                     + (f'тема <code>{pay}</code>' if pay
                        else '<b>туда же, куда регистрации</b>'))
    else:
        lines.append(f'{e("bell")} Уведомления: <b>в общий админ-чат</b>')

    lines.append('')
    lines.append(f'<blockquote>События его людей идут и в общий админ-чат '
                 f'(с пометкой метки и id того, кому засчитан приведённый), '
                 f'и сюда копией. Регистрации — в одну тему, пополнения, '
                 f'покупки и автопродления — в другую: в одной ленте '
                 f'оплаты тонут среди заходов.\n\nТриал без подписки на канал касается '
                 f'только тех, кто пришёл по этой ссылке; общий тумблер в '
                 f'«Бесплатный период» это не меняет.</blockquote>')

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("stats")} Статистика по дням', 'pstat', tag))
    kb.row(_btn(f'{e("cross")} Требовать подписку на канал'
                if partner.get('no_channel') else
                f'{e("trial")} Разрешить триал без подписки', 'pchan', tag))
    kb.row(_btn(f'{e("hot")} Снять повышенный процент' if boost
                else f'{e("hot")} Повышенный процент', 'pboost', tag))
    kb.row(_btn(f'{e("bell")} Чат и тема регистраций', 'pchat', tag))
    if chat_id:
        kb.row(_btn(f'{e("money")} Тема оплат', 'pchatpay', tag))
        kb.row(_btn(f'{e("broom")} Убрать чат', 'pchatoff', tag))
    kb.row(_btn(f'{e("trash")} Удалить метку', 'pdel', tag))
    kb.row(_btn(f'{e("back")} Назад', 'partners'))
    await edit(call, '\n'.join(lines), kb)


# ── статистика по дням ──────────────────────────────────────────────────────
#
# «Сколько всего привёл» на вопрос о партнёре не отвечает: по одному числу
# не видно ни всплеска после ролика, ни того, что ссылка неделю назад
# перестала работать. Отвечают дни подряд.

STAT_DAYS = 14
STAT_LONG = 30


def stats_text(data: dict, *, days: int) -> str:
    rows = data['days']
    top = max((row['people'] for row in rows), default=0)
    today = data['today']

    lines = [f'<b>{e("stats")} {data["tag"]} — по дням</b>', '',
             f'{e("referrals")} За {days} дн. пришло: '
             f'<b>{data["people"]}</b> чел.',
             f'{e("money")} Внесли за это время: <b>{data["paid"]}₽</b>',
             f'{e("calendar")} Сегодня: <b>{today["people"]}</b> чел., '
             f'<b>{today["paid"]}₽</b>',
             f'{e("friends")} Всего по метке: '
             f'<b>{data["total_people"]}</b> чел.', '']

    body = []
    for row in reversed(rows):           # свежий день сверху: его и смотрят
        bar = partner_stats.bar(row['people'], top)
        money = f'{row["paid"]}₽' if row['paid'] else ''
        body.append(f'{row["day"]} {row["people"]:>4} '
                    f'{bar:<{partner_stats.BAR_WIDTH}} {money}'.rstrip())
    lines.append('<code>' + '\n'.join(body) + '</code>')

    lines.append('')
    lines.append(f'<blockquote>Деньги — внесённое его людьми в этот день, '
                 f'включая пришедших раньше: человек из августа платит '
                 f'сегодня, и это сегодняшние деньги партнёра. Считается '
                 f'оплаченное, а не зачисленное — бонус за пополнение наш, '
                 f'и к его работе отношения не имеет.\n\n'
                 f'Люди — по метке в карточке. Перешедшие по обычной '
                 f'числовой ссылке того же человека сюда не попадают: это '
                 f'другая ссылка.</blockquote>')
    return '\n'.join(lines)


async def stats(call: types.CallbackQuery, callback_data: Adm,
                state: FSMContext, c, settings) -> None:
    from app.admin.panel import edit

    await state.clear()
    tag = domain.normalize(callback_data.a)
    if not await c.ref_tags.get(tag):
        await call.answer('Метка не найдена', show_alert=True)
        return

    long = callback_data.b == 'long'
    days = STAT_LONG if long else STAT_DAYS
    # Перебор всех его людей по базе в другой стране занимает секунды, и
    # молчащая кнопка в это время выглядит сломанной.
    await call.answer('Считаю…')
    data = await partner_stats.by_day(c.users, c.balance_log,
                                      tag=tag, days=days)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("calendar")} За {STAT_DAYS if long else STAT_LONG} дн.',
                'pstat', tag, '' if long else 'long'))
    kb.row(_btn(f'{e("back")} К партнёру', 'pshow', tag))
    await edit(call, stats_text(data, days=days), kb)


async def toggle_channel(call: types.CallbackQuery, callback_data: Adm,
                         state: FSMContext, c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    partner = await c.ref_tags.get(tag)
    if not partner:
        await call.answer('Метка не найдена', show_alert=True)
        return

    value = not partner.get('no_channel')
    await c.ref_tags.update(tag, no_channel=value)
    log.info('партнёр %s: триал без подписки = %s', tag, value)
    await call.answer('Подписка на канал больше не нужна' if value
                      else 'Подписка на канал снова обязательна')
    await card(call, callback_data, state, c, settings)


# ── чат уведомлений ─────────────────────────────────────────────────────────
CHAT_ASK = (
    f'{e("bell")} <b>Чат уведомлений</b>\n\n'
    f'Пришлите id чата и номер темы через пробел:\n'
    f'<code>-1001234567890 42</code>\n\n'
    f'Без темы — только id чата.\n\n'
    f'<blockquote>Где взять: отправьте <code>/chatid</code> внутри нужной '
    f'темы — бот ответит обоими числами. Бот должен быть в этом чате и '
    f'уметь писать в тему.\n\nУ супергрупп id начинается с '
    f'<code>-100</code>.</blockquote>'
)


async def ask_chat(call: types.CallbackQuery, callback_data: Adm,
                   state: FSMContext, c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    await state.set_state(Partner.chat)
    await state.update_data(tag=tag)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'pshow', tag))
    await call.message.edit_text(CHAT_ASK, reply_markup=kb.as_markup())
    await call.answer()


def parse_chat(text: str) -> tuple[int, int] | None:
    """«-1001234567890 42» → (чат, тема). Пусто — не разобрали."""
    parts = str(text or '').replace(',', ' ').split()
    numbers = [part for part in parts if part.lstrip('-').isdigit()]
    if not numbers:
        return None
    chat_id = int(numbers[0])
    topic = int(numbers[1]) if len(numbers) > 1 else 0
    return chat_id, topic


async def got_chat(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    tag = data.get('tag') or ''
    pair = parse_chat(message.text or '')
    if not pair:
        await message.answer(f'{e("warning")} Не вижу чисел. Пример: '
                             f'<code>-1001234567890 42</code>')
        return

    chat_id, topic = pair
    # Проверяем отправкой, а не верой на слово: неправильный id тихо
    # превратил бы весь партнёрский поток в строчки в логе.
    try:
        await message.bot.send_message(
            chat_id=chat_id, message_thread_id=topic or None,
            text=f'{e("ok")} Сюда будут приходить события по метке '
                 f'<code>{tag}</code>.')
    except Exception as exc:
        await message.answer(
            f'{e("cross")} Не получилось написать в этот чат:\n'
            f'<code>{exc}</code>\n\n'
            f'Добавьте бота в чат (и в тему), дайте право писать — и '
            f'пришлите числа ещё раз.')
        return

    await c.ref_tags.update(tag, chat_id=chat_id, topic_id=topic)
    await state.clear()
    log.info('партнёр %s: уведомления в %s (тема %s)', tag, chat_id, topic)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} К партнёру', 'pshow', tag))
    await message.answer(
        f'{e("ok")} Готово: события по метке <code>{tag}</code> пойдут в этот '
        f'чат. Проверочное сообщение туда уже ушло.',
        reply_markup=kb.as_markup())


BOOST_ASK = (
    f'{e("hot")} <b>Повышенный процент</b>\n\n'
    f'Пришлите процент и на сколько дней:\n'
    f'<code>50 7</code> — 50% на неделю\n\n'
    f'<blockquote>Действует на <b>всех его рефералов</b> — и пришедших по '
    f'этой метке, и по другой его ссылке, и приглашённых хоть год назад. '
    f'Акция объявляется человеку, а не ссылке.\n\n'
    f'Только пока идёт срок: потом процент сам вернётся к обычному. Срок '
    f'обязателен — повышенный процент без даты окончания однажды забывают '
    f'снять, и он тихо становится постоянным.\n\n'
    f'Уже начисленное не пересчитывается: проценты считаются в момент '
    f'оплаты.</blockquote>'
)

BOOST_MAX = 90
BOOST_MAX_DAYS = 90


async def toggle_boost(call: types.CallbackQuery, callback_data: Adm,
                       state: FSMContext, c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    partner = await c.ref_tags.get(tag) or {}

    if domain.boost_rate(partner, time_now()):
        await c.ref_tags.update(tag, boost_rate=0.0, boost_until=None)
        log.info('партнёр %s: повышенный процент снят', tag)
        await call.answer('Процент вернулся к обычному')
        await card(call, callback_data, state, c, settings)
        return

    await state.set_state(Partner.boost)
    await state.update_data(tag=tag)
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'pshow', tag))
    await call.message.edit_text(BOOST_ASK, reply_markup=kb.as_markup())
    await call.answer()


async def got_boost(message: types.Message, state: FSMContext, c,
                    settings) -> None:
    from datetime import timedelta

    data = await state.get_data()
    tag = data.get('tag') or ''
    parts = (message.text or '').replace(',', ' ').replace('%', ' ').split()
    numbers = [part for part in parts if part.isdigit()]

    if len(numbers) < 2:
        await message.answer(f'{e("warning")} Нужны два числа: процент и дни. '
                             f'Например: <code>50 7</code>')
        return

    percent, days = int(numbers[0]), int(numbers[1])
    base = round(await settings.rate('bonus.ref_rate') * 100)
    if not 1 <= percent <= BOOST_MAX:
        await message.answer(f'{e("warning")} Процент — от 1 до {BOOST_MAX}.')
        return
    if percent <= base:
        await message.answer(
            f'{e("warning")} Обычный процент и так {base}%. Повышенный '
            f'должен быть больше — иначе он ничего не меняет.')
        return
    if not 1 <= days <= BOOST_MAX_DAYS:
        await message.answer(f'{e("warning")} Дни — от 1 до {BOOST_MAX_DAYS}.')
        return

    until = time_now() + timedelta(days=days)
    await c.ref_tags.update(tag, boost_rate=percent / 100, boost_until=until)
    await state.clear()
    log.warning('партнёр %s: процент %s%% до %s', tag, percent, until)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} К партнёру', 'pshow', tag))
    await message.answer(
        f'{e("hot")} <b>{percent}% до {fmt(until, "%d.%m.%Y")}</b>\n\n'
        f'Действует на всех рефералов владельца метки <code>{tag}</code> — '
        f'включая приглашённых давно и пришедших по другим его ссылкам. '
        f'Потом процент сам вернётся к {base}%.\n\n'
        f'<blockquote>Он видит новый процент у себя в «Реферальной '
        f'программе» — со сроком.</blockquote>',
        reply_markup=kb.as_markup())


PAY_ASK = (
    f'{e("money")} <b>Тема оплат</b>\n\n'
    f'Пришлите номер темы — одним числом:\n'
    f'<code>43</code>\n\n'
    f'<blockquote>Чат тот же, меняется только тема. Туда пойдут '
    f'пополнения, покупки подписок и автопродления — регистрации '
    f'останутся в своей.\n\nНомер темы покажет <code>/chatid</code>, '
    f'отправленная внутрь неё. Ноль — сложить всё в одну тему '
    f'с регистрациями.</blockquote>'
)


async def ask_pay_topic(call: types.CallbackQuery, callback_data: Adm,
                        state: FSMContext, c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    if not (await c.ref_tags.get(tag) or {}).get('chat_id'):
        await call.answer('Сначала задайте чат уведомлений', show_alert=True)
        return

    await state.set_state(Partner.pay_topic)
    await state.update_data(tag=tag)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'pshow', tag))
    await call.message.edit_text(PAY_ASK, reply_markup=kb.as_markup())
    await call.answer()


async def got_pay_topic(message: types.Message, state: FSMContext, c,
                        settings) -> None:
    data = await state.get_data()
    tag = data.get('tag') or ''
    raw = (message.text or '').strip()
    if not raw.lstrip('-').isdigit():
        await message.answer(f'{e("warning")} Нужно число — номер темы. '
                             f'Например: <code>43</code>')
        return

    topic = max(0, int(raw))
    partner = await c.ref_tags.get(tag) or {}
    chat_id = int(partner.get('chat_id') or 0)

    if topic:
        # Та же проверка отправкой, что и для чата: тема могла быть закрыта
        # или удалена, и узнать об этом при первой же оплате — плохой план.
        try:
            await message.bot.send_message(
                chat_id=chat_id, message_thread_id=topic,
                text=f'{e("money")} Сюда будут приходить оплаты по метке '
                     f'<code>{tag}</code>.')
        except Exception as exc:
            await message.answer(
                f'{e("cross")} В эту тему написать не вышло:\n'
                f'<code>{exc}</code>\n\nПроверьте номер и права бота.')
            return

    await c.ref_tags.update(tag, topic_pay_id=topic)
    await state.clear()
    log.info('партнёр %s: тема оплат %s', tag, topic)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} К партнёру', 'pshow', tag))
    await message.answer(
        (f'{e("ok")} Оплаты по метке <code>{tag}</code> пойдут в тему '
         f'<code>{topic}</code>.' if topic else
         f'{e("ok")} Оплаты вернулись в тему регистраций.'),
        reply_markup=kb.as_markup())


async def clear_chat(call: types.CallbackQuery, callback_data: Adm,
                     state: FSMContext, c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    await c.ref_tags.update(tag, chat_id=0, topic_id=0, topic_pay_id=0)
    await call.answer('Уведомления вернулись в общий админ-чат')
    await card(call, callback_data, state, c, settings)


# ── новый партнёр ───────────────────────────────────────────────────────────
NEW_ASK = (
    f'{e("plus")} <b>Новый партнёр</b>\n\n'
    f'Пришлите метку и того, кому идут начисления:\n'
    f'<code>vlad 802421217</code>\n'
    f'<code>vlad @username блогер с ютуба</code>\n\n'
    f'<blockquote>Метка — латиница, цифры и подчёркивание. Ссылка станет '
    f'<code>t.me/бот?start=ref_vlad</code> и будет вести на того же '
    f'человека, что и его обычная реферальная.\n\nОдному человеку можно '
    f'выдать несколько меток — по одной на площадку, чтобы видеть, какая '
    f'работает.</blockquote>'
)


async def ask_new(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await state.set_state(Partner.new)
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'partners'))
    await call.message.edit_text(NEW_ASK, reply_markup=kb.as_markup())
    await call.answer()


async def got_new(message: types.Message, state: FSMContext, c, settings) -> None:
    parts = (message.text or '').split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(f'{e("warning")} Нужны метка и id: '
                             f'<code>vlad 802421217</code>')
        return

    raw_tag, target = parts[0], parts[1]
    note = parts[2].strip() if len(parts) > 2 else ''

    problem = domain.check(raw_tag)
    if problem:
        await message.answer(f'{e("cross")} {problem}')
        return

    owner = await c.moderation.find_user(target)
    if not owner:
        await message.answer(
            f'{e("cross")} Пользователь <code>{target}</code> не найден. '
            f'Он должен хотя бы раз зайти в бота.')
        return

    tag = domain.normalize(raw_tag)
    user_id = (owner.get('user_data') or {}).get('user_id')
    if not await c.ref_tags.create(tag, user_id, note,
                                   admin_id=message.from_user.id):
        busy = await c.ref_tags.owner(tag)
        await message.answer(
            f'{e("cross")} Метка <code>{tag}</code> уже занята'
            + (f' — ведёт на <code>{busy}</code>.' if busy else '.'))
        return

    await state.clear()
    log.info('партнёр %s заведён на %s', tag, user_id)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("settings")} Настроить', 'pshow', tag))
    kb.row(_btn(f'{e("back")} К партнёрам', 'partners'))
    await message.answer(
        f'{e("ok")} <b>Партнёр заведён</b>\n\n'
        f'{e("link")} <code>{domain.link(await settings.get("link.bot_username"), tag)}</code>\n'
        f'{e("user")} Начисления: <code>{ids.show(user_id)}</code>\n\n'
        f'<blockquote>Осталось решить два вопроса: нужен ли его людям '
        f'триал без подписки на канал и куда слать события по ним. '
        f'Обе кнопки — в карточке.</blockquote>',
        reply_markup=kb.as_markup())


# ── удаление ────────────────────────────────────────────────────────────────
async def ask_delete(call: types.CallbackQuery, callback_data: Adm, c,
                     settings) -> None:
    from app.admin.panel import edit

    tag = domain.normalize(callback_data.a)
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("trash")} Да, удалить {tag}', 'pdelok', tag))
    kb.row(_btn(f'{e("back")} Отмена', 'pshow', tag))
    await edit(call, f'{e("warning")} Удалить метку <code>{tag}</code>?\n\n'
                     f'<blockquote>Уже приглашённые остаются за тем, кто их '
                     f'привёл: связь хранится в их карточках. Перестанет '
                     f'работать сама ссылка — кто перейдёт по ней потом, '
                     f'зайдёт обычным новым человеком.</blockquote>', kb)


async def delete(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
                 c, settings) -> None:
    tag = domain.normalize(callback_data.a)
    await c.ref_tags.remove(tag)
    log.info('метка %s удалена админом %s', tag, call.from_user.id)
    await call.answer('Метка освобождена')
    await screen(call, state, c, settings)


def register(router: Router) -> None:
    router.callback_query.register(screen, Adm.filter(F.act == 'partners'))
    router.callback_query.register(card, Adm.filter(F.act == 'pshow'))
    router.callback_query.register(stats, Adm.filter(F.act == 'pstat'))
    router.callback_query.register(toggle_channel, Adm.filter(F.act == 'pchan'))
    router.callback_query.register(ask_chat, Adm.filter(F.act == 'pchat'))
    router.callback_query.register(toggle_boost, Adm.filter(F.act == 'pboost'))
    router.callback_query.register(ask_pay_topic, Adm.filter(F.act == 'pchatpay'))
    router.callback_query.register(clear_chat, Adm.filter(F.act == 'pchatoff'))
    router.callback_query.register(ask_new, Adm.filter(F.act == 'pnew'))
    router.callback_query.register(ask_delete, Adm.filter(F.act == 'pdel'))
    router.callback_query.register(delete, Adm.filter(F.act == 'pdelok'))
    router.message.register(got_chat, Partner.chat)
    router.message.register(got_pay_topic, Partner.pay_topic)
    router.message.register(got_boost, Partner.boost)
    router.message.register(got_new, Partner.new)
