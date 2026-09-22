"""Посты в канал от имени бота — и учёт того, кого они привели.

Пост в канале нужен не сам по себе, а чтобы из него пришли в бота. Поэтому
кнопка под постом здесь не украшение, а весь смысл: ссылку из текста не
нажимают и теряют при пересылке, а кнопку репост тащит с собой.

Ссылка в кнопке — не голый `t.me/бот`, а `t.me/бот?start=post_2209_1430`.
Этот хвост Telegram отдаёт боту при первом входе, и он оседает в карточке
человека (`user_data.utm`). Без него на вопрос «что дал вчерашний пост»
ответить нечем: регистрации за день есть, а откуда они — нет.

Пост не редактируется и не удаляется отсюда нарочно: опубликованное
сообщение правится в самом Telegram, а список здесь — журнал, по которому
считают результат.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core import db as names
from app.core.time import now as time_now

log = logging.getLogger(__name__)

# Префикс метки. По нему же метка отличается от реферальной (`ref_`):
# пост канала ведёт не «от человека», а «от нас», и рефералку он не трогает.
PREFIX = 'post_'


async def channel_of(settings) -> str:
    """Куда публиковать. Пусто в своей настройке — берём канал проверки
    подписки: он тот же самый, и держать два одинаковых поля незачем."""
    raw = str(await settings.get('post.channel') or '').strip()
    return raw or str(await settings.get('trial.channel') or '').strip()


def username_of(channel: str) -> str:
    """'@rsconnect_vpn' → 'rsconnect_vpn'. Числовой id имени не имеет."""
    raw = (channel or '').strip()
    if raw.startswith('https://t.me/'):
        raw = raw[len('https://t.me/'):].strip('/')
    return raw.lstrip('@') if raw and not raw.lstrip('-').isdigit() else ''


def post_url(channel: str, message_id) -> str:
    """Ссылка на опубликованный пост. Для канала с числовым id — пустая:
    придумывать её нельзя, а показывать неработающую — хуже, чем никакой."""
    name = username_of(channel)
    return f'https://t.me/{name}/{int(message_id)}' if name and message_id else ''


def start_link(bot_username: str, tag: str = '') -> str:
    """Кнопка ведёт сюда. Без метки — просто в бота."""
    name = (bot_username or '').strip().lstrip('@')
    if not name:
        return ''
    return f'https://t.me/{name}?start={tag}' if tag else f'https://t.me/{name}'


def make_tag(moment: datetime | None = None) -> str:
    """Метка поста: день и время публикации. Читается глазами, и по ней
    видно, какой именно пост считается, без похода в журнал."""
    return PREFIX + (moment or time_now()).strftime('%d%m_%H%M')


async def unique_tag(db, moment: datetime | None = None) -> str:
    """Два поста в одну минуту — редкость, но их метки не должны слиться:
    иначе оба покажут одну сумму регистраций, и оба соврут."""
    base = make_tag(moment)
    tag, attempt = base, 1
    while await db[names.CHANNEL_POSTS].count_documents({'tag': tag}):
        attempt += 1
        tag = f'{base}_{attempt}'
    return tag


async def remember(db, *, tag: str, text: str, channel: str, message_id,
                   button: str, url: str, admin_id: int, photo: str = '',
                   photos=(), album: bool = False,
                   at: datetime | None = None) -> dict:
    row = {
        '_id': tag, 'tag': tag, 'text': text, 'photo': photo,
        'photos': list(photos or ([photo] if photo else [])), 'album': album,
        'channel': channel, 'message_id': message_id, 'button': button,
        'url': url, 'link': post_url(channel, message_id),
        'admin_id': admin_id, 'at': at or time_now(),
    }
    await db[names.CHANNEL_POSTS].insert_one(row)
    return row


async def history(db, limit: int = 20) -> list[dict]:
    rows = await db[names.CHANNEL_POSTS].find({}).to_list(length=None)
    return sorted(rows, key=lambda row: row.get('at') or time_now(),
                  reverse=True)[:limit]


async def came_from(users, tag: str) -> int:
    """Сколько человек пришло по этой метке. Считается по карточкам, а не
    по счётчику: счётчик можно потерять при перезапуске, карточки — нет."""
    if not tag:
        return 0
    return await users.col.count_documents({'user_data.utm': tag})


# Пределы Telegram: подпись к картинке и текст без картинки — разные.
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


def preview_problem(text: str, photos=()) -> str:
    """Почему пост отправить нельзя. Пустая строка — можно.

    Отказ называет, на сколько знаков не влезло, и что с этим делать:
    «длиннее 1024» без числа и без выхода — это тупик, а пост в этот момент
    уже написан.
    """
    photos = list(photos or [])
    length = len(text)

    if photos:
        if length <= CAPTION_LIMIT:
            return ''
        return (f'Подпись к картинке у Telegram не длиннее '
                f'{CAPTION_LIMIT} знаков, а здесь {length} — лишних '
                f'{length - CAPTION_LIMIT}.\n\nВыходы: сократить на '
                f'{length - CAPTION_LIMIT} знаков; или отправить пост без '
                f'картинки — текстом влезает {TEXT_LIMIT} знаков, и кнопка '
                f'при нём остаётся.')

    if not text.strip():
        return 'Пустой пост отправить нельзя.'
    if length > TEXT_LIMIT:
        return (f'Текст у Telegram не длиннее {TEXT_LIMIT} знаков, а здесь '
                f'{length} — лишних {length - TEXT_LIMIT}.')
    return ''
