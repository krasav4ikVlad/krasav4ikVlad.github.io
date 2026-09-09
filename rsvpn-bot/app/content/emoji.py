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

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Значок и id по умолчанию. Пустая строка — останется обычный значок.
#
# Свои id лучше держать не здесь, а в emoji_ids.json рядом с .env: этот файл
# при обновлении бота перезаписывается, а тот — нет. См. IDS_FILE ниже.
#
# Где взять id: перешлите сообщение с эмодзи боту вроде @idstickerbot, либо
# возьмите из старого кода — там они проставлены прямо в тегах tg-emoji.
# ─────────────────────────────────────────────────────────────────────────────
EMOJI: dict[str, tuple[str, str]] = {
    # имя             значок  id кастомного эмодзи
    # ── набор RS VPN ─────────────────────────────────────────────────────────
    'back':           ('⬅️', '5454031772071273791'),
    'id':             ('🆔', '5316813090292015176'),
    'money':          ('💰', '5456492002352865203'),
    'friends':        ('👥', '5454001191904124816'),
    'email':          ('✉️', '5454364043626198209'),
    'calendar':       ('📅', '5454021592998785711'),
    'shield':         ('🛡', '5456342558965800638'),
    'devices':        ('📲', '5454361750113657125'),
    'link':           ('🔗', '5456287261261864709'),
    'support':        ('🤖', '5453938459611801110'),
    'channel':        ('📢', '5456598045095405560'),
    'warning':        ('❗️', '5456341446569273442'),
    'gift':           ('🎁', '5456582016277455413'),
    'traffic':        ('🔋', '5453936591301027381'),
    'payout':         ('💸', '5454337045461771476'),
    'user':           ('👤', '5456111481135340159'),

    # ── остальные: где id пусто, показывается обычный значок ─────────────────
    'ok':             ('✅', '5454261381022920917'),
    'cross':          ('❌', ''),
    'down':           ('👇', ''),
    'renew':          ('🔁', '5454346756382829527'),
    'skull':          ('💀', ''),
    'new':            ('🆕', ''),
    'card':           ('💳', ''),
    'attention':      ('⚠️', ''),
    'ban':            ('🚫', ''),
    'hardban':        ('⛔️', ''),
    'edit':           ('✏️', '5456198694741252869'),
    'plus':           ('➕', '5456571497902544905'),
    'minus':          ('➖', '5456277026354798878'),
    'broadcast':      ('💬', ''),
    'sbp':            ('⚡️', ''),
    'referrals':      ('🫂', '5454001191904124816'),
    'clock':          ('⏰', ''),
    'settings':       ('⚙️', ''),
    'trash':          ('🗑', '5454286871653822631'),
    'hot':            ('🔥', '5456486547744401445'),
    'broom':          ('🧹', ''),
    'withdraw':       ('📤', ''),
    'document':       ('📄', ''),
    'promo':          ('🎟', '5456334265383951620'),
    'trial':          ('🧪', ''),
    'green':          ('🟢', '5456351020051370999'),
    'yellow':         ('🟡', ''),
    'white':          ('⚪️', ''),
    'mail':           ('📩', '5454364043626198209'),
    'stats':          ('📊', '5454189122493148129'),
    'globe':          ('🌐', ''),
    # Раздел «Свой сервер». Отдельным именем, а не 'globe': значок
    # у них один, но смысл разный — поменяют один, второй не поедет.
    'private':        ('🌐', ''),
    'pin':            ('📍', ''),
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
    'cart':           ('🛒', ''),
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
    'crypto':         ('💸', '5454337045461771476'),
    'discount':       ('🏷', ''),

    # ── сапёр на экране техработ ─────────────────────────────────────────────
    'cell':           ('⬜', ''),
    'flag':           ('🚩', ''),
    'mine':           ('💣', ''),
    'boom':           ('💥', ''),
    'trophy':         ('🏆', ''),
    'wrench':         ('🔧', ''),

    # ── «+1»…«+5» на кнопках менеджера устройств ─────────────────────────────
    # Значок в таблице — цифра в рамке: у «плюс один» своего символа в
    # юникоде нет, а нужен разный на каждое число — иначе не отличить, какой
    # кастомный эмодзи подставлять. Видно его только при выключенных
    # кастомных: обычно на кнопке рисуется иконка по id.
    'plus1':          ('1️⃣', '5454008003722258172'),
    'plus2':          ('2️⃣', '5456207198776500494'),
    'plus3':          ('3️⃣', '5454171483062530757'),
    'plus4':          ('4️⃣', '5454243896211056682'),
    'plus5':          ('5️⃣', '5456287050808466326'),
}

BY_CHAR: dict[str, str] = {}
_PATTERN = re.compile(r'(?!x)x')      # заполняется в _rebuild()


def _rebuild() -> None:
    """Пересобрать обратный указатель символ → id."""
    BY_CHAR.clear()
    for name, (char, emoji_id) in EMOJI.items():
        # один символ у двух имён (payout и crypto — оба 💸): в тег
        # превращаем по первому, id у них всё равно общий
        BY_CHAR.setdefault(char, emoji_id)

    # Длинные символы вперёд: «⚠️» начинается с «⚠», и без сортировки
    # короткий вариант съел бы модификатор.
    global _PATTERN
    _PATTERN = re.compile('|'.join(re.escape(char) for char in
                                   sorted(BY_CHAR, key=len, reverse=True)))


_rebuild()


# ── свои id: отдельным файлом, а не правкой этого ───────────────────────────
# Таблица выше — исходник, и обновление бота её перезапишет. Свои id держите
# в emoji_ids.json рядом с .env: обновления его не трогают, потому что он
# вне git, как .env и media/.
#
#     {"back": "5321133913291135005", "money": "5317017827088049911"}
#
# Выгрузить текущие: python -m scripts.emoji_ids --export
IDS_FILE = 'emoji_ids.json'


# Что файл перебил у кода: имя → (было в коде, стало из файла). Нужно, чтобы
# было видно расхождение: файл всегда сильнее, и после обновления бота он
# продолжит держать старые id, пока про него не вспомнят.
OVERRIDDEN: dict[str, tuple[str, str]] = {}


def set_ids(values: dict[str, str]) -> int:
    """Подставить id по именам. Возвращает, сколько применилось."""
    applied = 0
    for name, emoji_id in (values or {}).items():
        entry = EMOJI.get(name)
        if entry is None:
            log.warning('в %s есть незнакомое имя значка: %s', IDS_FILE, name)
            continue
        emoji_id = str(emoji_id or '').strip()
        if not emoji_id:
            continue
        if entry[1] and entry[1] != emoji_id:
            OVERRIDDEN[name] = (entry[1], emoji_id)
        EMOJI[name] = (entry[0], emoji_id)
        applied += 1

    _rebuild()
    return applied


def load_ids(path: str | Path = IDS_FILE) -> int:
    """Прочитать emoji_ids.json, если он есть. Нет — молча ничего.

    Ищется рядом с .env, а если запустили из подпапки — рядом с
    pyproject.toml: тот же порядок, что у config.load_env_file().
    """
    file = Path(path)
    if not file.is_file():
        file = Path(__file__).resolve().parents[2] / IDS_FILE
    if not file.is_file():
        return 0

    try:
        values = json.loads(file.read_text(encoding='utf-8'))
    except Exception as exc:      # битый файл не должен ронять бота
        log.warning('%s не прочитан, значки останутся обычными: %s', file, exc)
        return 0

    if not isinstance(values, dict):
        log.warning('%s: ожидался объект вида {"имя": "id"}', file)
        return 0
    return set_ids(values)


load_ids()

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
