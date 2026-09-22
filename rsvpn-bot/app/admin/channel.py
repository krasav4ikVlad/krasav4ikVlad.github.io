"""Пост в канал от имени бота: написал, посмотрел, отправил.

Раньше пост в канал писали руками, а кнопку под ним приходилось доделывать
через @ControllerBot — то есть отдавать сторонему боту право публиковать в
своём канале. Здесь это делает наш бот: он и так админ канала (иначе не
проверил бы подписку для бесплатного периода).

Кнопка под постом обязательна и добавляется сама. Пост канала без кнопки
уводит человека искать бота поиском, а из поиска доходят единицы.

Предпросмотр тоже обязателен: пост уходит подписчикам и правится потом
только в Telegram, поэтому увидеть настоящее сообщение — с картинкой,
разметкой и кнопкой — нужно до отправки, а не после.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt
from app.services import channel as service

log = logging.getLogger(__name__)

USAGE = (
    f'{e("channel")} <b>Пост в канал</b>\n\n'
    f'Пришлите пост одним сообщением: текст или картинку с подписью. '
    f'Работает разметка Telegram — жирный, курсив, ссылки.\n\n'
    f'<blockquote>Кнопку в бота бот добавит сам, её надпись можно поменять '
    f'на следующем шаге. В ссылке кнопки будет метка этого поста — по ней '
    f'потом видно, сколько человек он привёл: <code>/posts</code>.\n\n'
    f'Перед отправкой покажу пост целиком: так, как его увидят '
    f'подписчики.</blockquote>'
)


class Post(StatesGroup):
    body = State()
    button = State()


def _btn(text: str, act: str, a: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=Adm(act=act, a=a).pack())


# ── сбор поста ──────────────────────────────────────────────────────────────
async def ask_body(event, state: FSMContext, settings) -> None:
    await state.clear()
    await state.set_state(Post.body)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Отмена', 'main'))
    text = USAGE + f'\n\n{e("link")} Канал: <code>{await service.channel_of(settings)}</code>'

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb.as_markup())
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb.as_markup())


async def command(message: types.Message, state: FSMContext, c, settings) -> None:
    await ask_body(message, state, settings)


async def screen(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await ask_body(call, state, settings)


async def got_body(message: types.Message, state: FSMContext, c, settings) -> None:
    photo = message.photo[-1].file_id if message.photo else ''
    text = message.html_text or ''

    problem = service.preview_problem(text, photo)
    if problem:
        await message.answer(f'{e("warning")} {problem}')
        return

    await state.update_data(text=text, photo=photo)
    await show_preview(message, state, c, settings)


async def ask_button(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    await state.set_state(Post.button)
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("back")} Оставить как есть', 'postback'))
    await call.message.answer(
        f'{e("edit")} Пришлите надпись для кнопки — коротко, до 30 знаков.\n\n'
        f'<blockquote>Надпись, которая обещает действие («Подключить VPN», '
        f'«Забрать 3 дня»), работает лучше названия («RS VPN»).</blockquote>',
        reply_markup=kb.as_markup())
    await call.answer()


async def got_button(message: types.Message, state: FSMContext, c, settings) -> None:
    label = (message.text or '').strip()
    if not label:
        await message.answer(f'{e("warning")} Надпись пустая.')
        return
    if len(label) > 30:
        await message.answer(f'{e("warning")} Слишком длинно: {len(label)} знаков '
                             f'из 30. Кнопка обрежется.')
        return

    await state.update_data(button=label)
    await show_preview(message, state, c, settings)


# ── предпросмотр ────────────────────────────────────────────────────────────
async def post_markup(c, settings, data: dict) -> types.InlineKeyboardMarkup:
    """Та самая кнопка, что уйдёт в канал."""
    label = data.get('button') or str(await settings.get('post.button') or 'Открыть бота')
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text=label, url=data['url']))
    return kb.as_markup()


async def show_preview(message: types.Message, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    if not data.get('tag'):
        # Метка заводится один раз на пост: пересбор предпросмотра не должен
        # менять ссылку, иначе в канал уйдёт одна метка, а в журнал другая.
        tag = await service.unique_tag(c.db)
        url = service.start_link(str(await settings.get('link.bot_username')), tag)
        await state.update_data(tag=tag, url=url)
        data = await state.get_data()

    markup = await post_markup(c, settings, data)
    try:
        if data.get('photo'):
            await message.answer_photo(data['photo'], caption=data['text'],
                                       reply_markup=markup)
        else:
            await message.answer(data['text'], reply_markup=markup,
                                 disable_web_page_preview=False)
    except Exception as exc:
        await message.answer(
            f'{e("warning")} Telegram не принял это сообщение:\n<code>{exc}</code>\n\n'
            f'Чаще всего это сломанная разметка. Поправьте и пришлите заново.')
        return

    await state.set_state(Post.body)

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("ok")} Отправить в канал', 'postgo'))
    kb.row(_btn(f'{e("edit")} Надпись на кнопке', 'postbtn'))
    kb.row(_btn(f'{e("cross")} Отмена', 'main'))
    await message.answer(
        f'{e("up_finger")} Так увидят подписчики.\n\n'
        f'{e("channel")} Канал: <code>{await service.channel_of(settings)}</code>\n'
        f'{e("link")} Метка: <code>{data["tag"]}</code>\n\n'
        f'<blockquote>Пришлите другое сообщение — предпросмотр '
        f'пересоберётся.</blockquote>',
        reply_markup=kb.as_markup())


async def back_to_preview(call: types.CallbackQuery, state: FSMContext, c,
                          settings) -> None:
    await state.set_state(Post.body)
    await show_preview(call.message, state, c, settings)
    await call.answer()


# ── отправка ────────────────────────────────────────────────────────────────
async def publish(call: types.CallbackQuery, state: FSMContext, c, settings) -> None:
    data = await state.get_data()
    if not data.get('text') and not data.get('photo'):
        await call.answer('Пост потерялся, начните заново', show_alert=True)
        return

    target = await service.channel_of(settings)
    if not target:
        await call.answer('Не задан канал: /admin → Посты в канал', show_alert=True)
        return

    markup = await post_markup(c, settings, data)
    await call.answer('Публикую')
    try:
        if data.get('photo'):
            sent = await call.bot.send_photo(target, data['photo'],
                                             caption=data['text'], reply_markup=markup)
        else:
            sent = await call.bot.send_message(target, data['text'],
                                               reply_markup=markup)
    except Exception as exc:
        log.error('пост в канал %s не ушёл: %r', target, exc)
        await call.message.answer(
            f'{e("cross")} <b>Telegram отказал</b>\n\n<code>{exc}</code>\n\n'
            f'<blockquote>Обычно причина одна: бот не админ канала или у него '
            f'нет права публиковать сообщения. Канал сейчас — '
            f'<code>{target}</code>, поменять можно в /admin → Посты в канал. '
            f'Текст поста не потерян: нажмите «Отправить» ещё раз после '
            f'исправления.</blockquote>')
        return

    row = await service.remember(
        c.db, tag=data['tag'], text=data.get('text') or '', channel=target,
        message_id=sent.message_id, button=(await _label(settings, data)),
        url=data['url'], admin_id=call.from_user.id, photo=data.get('photo') or '')
    await state.clear()
    log.info('пост %s опубликован в %s (%s)', row['tag'], target, sent.message_id)

    kb = InlineKeyboardBuilder()
    if row['link']:
        kb.row(types.InlineKeyboardButton(text=f'{e("channel")} Открыть пост',
                                          url=row['link']))
    kb.row(_btn(f'{e("clipboard")} Что дали посты', 'posts'))
    kb.row(_btn(f'{e("back")} В админку', 'main'))

    await call.message.answer(
        f'{e("ok")} <b>Пост опубликован</b>\n\n'
        f'{e("channel")} Канал: <code>{target}</code>\n'
        f'{e("link")} Метка: <code>{row["tag"]}</code>\n\n'
        f'<blockquote>Сколько человек он привёл, покажет <code>/posts</code> — '
        f'считается по тем, кто вошёл в бота с этой кнопки. Первые переходы '
        f'появятся в течение нескольких минут.\n\n'
        f'Опечатку правьте прямо в канале: сообщение наше, Telegram даёт его '
        f'редактировать. Кнопка при этом останется.</blockquote>',
        reply_markup=kb.as_markup())


async def _label(settings, data: dict) -> str:
    return data.get('button') or str(await settings.get('post.button') or 'Открыть бота')


# ── что дали посты ──────────────────────────────────────────────────────────
async def report(c, settings, limit: int = 20) -> str:
    rows = await service.history(c.db, limit)
    if not rows:
        return (f'{e("channel")} <b>Посты в канал</b>\n\n'
                f'Отсюда ещё ничего не публиковали.\n\n'
                f'<blockquote>Новый пост — <code>/post</code>. Кнопку в бота '
                f'и метку для подсчёта бот добавит сам.</blockquote>')

    lines = [f'{e("channel")} <b>Посты в канал</b>', '']
    total = 0
    for row in rows:
        came = await service.came_from(c.users, row.get('tag') or '')
        total += came
        head = (row.get('text') or '').replace('\n', ' ')
        head = _strip_tags(head)[:60]
        link = row.get('link') or ''
        title = f'<a href="{link}">{fmt(row.get("at"))}</a>' if link else fmt(row.get('at'))
        lines.append(f'{title} — <b>{came}</b> чел.')
        lines.append(f'   <i>{head}…</i>' if head else '   <i>картинка</i>')

    lines.append('')
    lines.append(f'{e("referrals")} Всего пришло с постов: <b>{total}</b>')
    lines.append('')
    lines.append('<blockquote>Считаются те, кто впервые вошёл в бота по кнопке '
                 'под постом. Подписчик, который был в боте раньше, сюда не '
                 'попадёт — это не потеря счёта, а разные вопросы: «сколько '
                 'новых» и «сколько прочитали».</blockquote>')
    return '\n'.join(lines)


def _strip_tags(text: str) -> str:
    """Заголовок строки — без разметки: иначе в список попадёт кусок тега."""
    out, skip = [], False
    for char in text:
        if char == '<':
            skip = True
        elif char == '>':
            skip = False
        elif not skip:
            out.append(char)
    return ''.join(out).strip()


async def posts_command(message: types.Message, c, settings) -> None:
    await message.answer(await report(c, settings), disable_web_page_preview=True)


async def posts_screen(call: types.CallbackQuery, c, settings) -> None:
    from app.admin.panel import edit

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("channel")} Новый пост', 'post'))
    kb.row(_btn(f'{e("back")} Назад', 'main'))
    await edit(call, await report(c, settings), kb)


def register(router: Router) -> None:
    router.message.register(command, Command('post'))
    router.message.register(posts_command, Command('posts'))
    router.callback_query.register(screen, Adm.filter(F.act == 'post'))
    router.callback_query.register(posts_screen, Adm.filter(F.act == 'posts'))
    router.callback_query.register(ask_button, Adm.filter(F.act == 'postbtn'))
    router.callback_query.register(back_to_preview, Adm.filter(F.act == 'postback'))
    router.callback_query.register(publish, Adm.filter(F.act == 'postgo'))
    router.message.register(got_button, Post.button)
    router.message.register(got_body, Post.body)
