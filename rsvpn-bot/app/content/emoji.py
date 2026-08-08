"""Все значки бота в одном месте.

Раньше каждый писался целиком прямо в строке:

    <tg-emoji emoji-id="5321133913291135005">⬅️</tg-emoji>

Восемьдесят значков на четыреста строк по сорока файлам — поменять один id
значило пройти по всему проекту. Здесь id и сам символ заданы один раз, а в
коде стоит имя:

    from app.content.emoji import e
    f'{e("money")} Баланс: ...'

Почему `e()` возвращает голый символ, а не готовый тег
─────────────────────────────────────────────────────
Один и тот же `e("back")` стоит и в тексте сообщения, и в подписи кнопки,
а кастомным эмодзи они становятся по-разному:

* текст и подпись фото — тег <tg-emoji> в HTML;
* подпись кнопки — HTML там не разбирается, тег показался бы буквами;
  зато у InlineKeyboardButton есть поле icon_custom_emoji_id;
* всплывающий ответ на нажатие — не поддерживает ни того, ни другого.

Выбрать способ на месте вызова нельзя: `e()` не знает, куда попадёт строка.
Поэтому здесь только символ, а способ выбирается на выходе — в middleware
сессии бота (app/bot/middlewares/emoji.py), где уже видно, какой это метод
API и какое поле.

Итого: в коде всегда `e("имя")`, а где это станет кастомным эмодзи —
решается автоматически и правильно.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

# ─────────────────────────────────────────────────────────────────────────────
# ЗАПОЛНИТЕ ID ЗДЕСЬ. Пустая строка — останется обычный значок, это нормально.
#
# Где взять id: перешлите сообщение с эмодзи боту вроде @idstickerbot, либо
# возьмите из старого кода — там они проставлены прямо в тегах tg-emoji.
# ─────────────────────────────────────────────────────────────────────────────
EMOJI: dict[str, tuple[str, str]] = {
    # имя             значок  id кастомного эмодзи
    # ── взяты из старого бота ────────────────────────────────────────────────
    'back':           ('⬅️', '5321133913291135005'),
    'id':             ('🆔', '5316813090292015176'),
    'money':          ('💰', '5317017827088049911'),
    'friends':        ('👥', '5316564321491260174'),
    'email':          ('✉️', '5316596297522781199'),
    'calendar':       ('📅', '5321162912910319482'),
    'shield':         ('🛡', '5319082443637037842'),
    'devices':        ('📲', '5318881086980265568'),
    'link':           ('🔗', '5318920660808932171'),
    'support':        ('🤖', '5318943836452462111'),
    'channel':        ('📢', '5321530076779551906'),
    'warning':        ('❗️', '5321083387295869258'),
    'gift':           ('🎁', '5318890458598906378'),
    'traffic':        ('🔋', '5321530974427717784'),
    'payout':         ('💸', '5319108887750679115'),
    'user':           ('👤', '5316934955694072975'),

    # ── id пока нет: выводится обычный значок ────────────────────────────────
    'ok':             ('✅', ''),
    'cross':          ('❌', ''),
    'down':           ('👇', ''),
    'renew':          ('🔁', ''),
    'skull':          ('💀', ''),
    'new':            ('🆕', ''),
    'card':           ('💳', ''),
    'attention':      ('⚠️', ''),
    'ban':            ('🚫', ''),
    'hardban':        ('⛔️', ''),
    'edit':           ('✏️', ''),
    'plus':           ('➕', ''),
    'minus':          ('➖', ''),
    'broadcast':      ('💬', ''),
    'sbp':            ('⚡️', ''),
    'referrals':      ('🫂', ''),
    'clock':          ('⏰', ''),
    'settings':       ('⚙️', ''),
    'trash':          ('🗑', ''),
    'hot':            ('🔥', ''),
    'broom':          ('🧹', ''),
    'withdraw':       ('📤', ''),
    'document':       ('📄', ''),
    'promo':          ('🎟', ''),
    'trial':          ('🧪', ''),
    'green':          ('🟢', ''),
    'yellow':         ('🟡', ''),
    'white':          ('⚪', ''),
    'mail':           ('📩', ''),
    'stats':          ('📊', ''),
    'globe':          ('🌐', ''),
    'puzzle':         ('🧩', ''),
    'note':           ('📝', ''),
    'hourglass':      ('⏳', ''),
    'target':         ('🎯', ''),
    'refresh':        ('🔄', ''),
    'reset':          ('♻️', ''),
    'up':             ('⬆️', ''),
    'down_arrow':     ('⬇️', ''),
    'question':       ('❔', ''),
    'tools':          ('🛠', ''),
    'clipboard':      ('📋', ''),
    'speaking':       ('🗣', ''),
    'growth':         ('📈', ''),
    'exchange':       ('💱', ''),
    'lock':           ('🔒', ''),
    'envelope':       ('📨', ''),
    'rocket':         ('🚀', ''),
    'wave':           ('👋', ''),
    'smile':          ('😊', ''),
    'receipt':        ('🧾', ''),
    'features':       ('🎛', ''),
    'servers':        ('🖥', ''),
    'design':         ('🎨', ''),
    'bypass':         ('🚧', ''),
    'knot':           ('🪢', ''),
    'bell':           ('🔔', ''),
    'megaphone':      ('📣', ''),
    'up_finger':      ('☝️', ''),
    'crypto':         ('💸', ''),
}

BY_CHAR: dict[str, str] = {}
for _name, (_char, _id) in EMOJI.items():
    # один символ у двух имён (payout и crypto — оба 💸): в тег превращаем
    # по первому, id у них всё равно общий
    BY_CHAR.setdefault(_char, _id)

# Длинные символы вперёд: «⚠️» начинается с «⚠», и без сортировки
# короткий вариант съел бы модификатор.
_PATTERN = re.compile('|'.join(re.escape(char) for char in
                               sorted(BY_CHAR, key=len, reverse=True)))

_enabled = True


def set_enabled(value: bool) -> None:
    """Тумблер из админки: content.custom_emoji."""
    global _enabled
    _enabled = bool(value)


def enabled() -> bool:
    return _enabled


# ── обычные значки на время одного сценария ─────────────────────────────────
# Тумблер выше — общий, а этот флаг живёт внутри одного обработчика.
# Нужен админке: если Telegram начнёт отклонять сообщения с кастомными
# эмодзи (право их слать есть не у каждого бота, и id иногда протухают),
# бот замолчит целиком — включая тот самый экран, где тумблер и лежит.
# Админка ходит на обычных значках всегда, поэтому выключить есть откуда.
_plain: ContextVar[bool] = ContextVar('plain_emoji', default=False)


@contextmanager
def plain(value: bool = True):
    """Внутри блока значки уходят как есть, без тегов и иконок."""
    token = _plain.set(value)
    try:
        yield
    finally:
        _plain.reset(token)


def is_plain() -> bool:
    return _plain.get()


def e(name: str) -> str:
    """Символ по имени. Тег навешивается позже, на выходе из бота.

    Неизвестное имя возвращается как есть — опечатка станет видна на экране,
    а не превратится в исключение посреди отрисовки чужого профиля.
    """
    found = EMOJI.get(name)
    return found[0] if found else name


def decorate(text: str) -> str:
    """Подменить известные символы кастомными эмодзи. Для текста сообщений.

    Внутрь <code> и <pre> не лезем: там разметка не вложена, и Telegram
    отклонил бы такое сообщение целиком.
    """
    if not _enabled or not text:
        return text

    def replace(match: re.Match) -> str:
        emoji_id = BY_CHAR.get(match.group(0), '')
        if not emoji_id:
            return match.group(0)
        return f'<tg-emoji emoji-id="{emoji_id}">{match.group(0)}</tg-emoji>'

    return ''.join(part if i % 2 else _PATTERN.sub(replace, part)
                   for i, part in enumerate(_split_protected(text)))


_PROTECTED = re.compile(r'(<code>.*?</code>|<pre>.*?</pre>)', re.DOTALL)


def _split_protected(text: str) -> list[str]:
    """Чётные куски — обычный текст, нечётные — <code>/<pre>, их не трогаем."""
    return _PROTECTED.split(text)


def leading_emoji_id(text: str) -> tuple[str, str]:
    """Значок в начале строки → (его id, остаток текста).

    Нужно подписям кнопок: HTML там не работает, а поле
    icon_custom_emoji_id — работает. Значок без id остаётся в тексте:
    иконки для него всё равно нет, а символ лучше, чем ничего.
    """
    match = _PATTERN.match(text)
    if not match:
        return '', text

    emoji_id = BY_CHAR.get(match.group(0), '')
    if not emoji_id:
        return '', text
    return emoji_id, text[match.end():].lstrip()


def missing_ids() -> list[str]:
    """Имена без заданного id — чтобы было видно, что осталось заполнить."""
    return sorted(name for name, (_, emoji_id) in EMOJI.items() if not emoji_id)
