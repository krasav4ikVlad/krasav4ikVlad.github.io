"""Главное меню в виде Rich Message — на пробу.

Rich Messages появились в Bot API 10.1 (июнь 2026), кнопки внутри текста —
в 10.3 (август). Это не картинка и не Mini App: сообщение остаётся текстом,
которое можно копировать и читать с экрана, но внутри у него заголовки,
картинка, сворачиваемые блоки и кнопки прямо в потоке текста.

Здесь собран один экран — профиль, он же главное меню, — чтобы посмотреть
на него живьём и решить, переводить ли на такой вид весь бот. Данные
настоящие, кнопки настоящие: нажимаются и ведут в те же разделы, что
обычное меню.

Разметка — HTML (поле `html` у InputRichMessage). Блочный способ
(`blocks`) даёт то же самое объектами, но читать его в коде втрое длиннее,
а чинить вёрстку приходится как раз глазами.

Картинка
────────
В HTML она ставится тегом <img>, но src — не путь к файлу: либо внешняя
ссылка http(s), либо `tg://photo?id=<id>`, а сам файл уезжает отдельным
списком `media` у InputRichMessage (InputRichMessageMedia с тем же id).
Второй способ и используется: картинки бота лежат в media/ на сервере,
наружу они не опубликованы, а file_id из кэша Telegram принимает как
обычно — повторная отправка снова не грузит PNG.

Важное ограничение API: «Images, videos, and audio files can be specified
only as separate media blocks» — внутрь <p> картинку вставлять нельзя,
только отдельным блоком. Поэтому она идёт самой первой строкой, как шапка
на остальных экранах бота.
"""

from __future__ import annotations

from html import escape

from app.bot.callbacks import Menu
from app.content import ids
from app.content.emoji import e
from app.core.time import fmt, now, parse_dt

# Кнопок в ряду — не больше восьми (ограничение Bot API), но на телефоне
# больше трёх уже не читается.
ROW = 3

# id картинки внутри сообщения. По документации — 1–64 знака, только
# A-Z, a-z, 0-9, _ и -: имя файла или путь сюда не годятся.
PHOTO = 'menu'
PHOTO_LINK = f'tg://photo?id={PHOTO}'


def button(text: str, screen: str, style: str = '') -> str:
    """Кнопка, ведущая в тот же раздел, что и обычная клавиатура."""
    data = escape(Menu(screen=screen).pack(), quote=True)
    styled = f' style="{style}"' if style else ''
    return f'<tg-button type="callback_data"{styled} data="{data}">{text}</tg-button>'


def row(*buttons: str, align: str = 'center') -> str:
    return (f'<tg-button-row align="{align}">'
            + ''.join(b for b in buttons if b) + '</tg-button-row>')


def image(src: str) -> str:
    """Картинка — отдельным блоком: внутри абзаца Telegram её не примет."""
    if not src:
        return ''
    return f'<img src="{escape(src, quote=True)}"/>'


def _days_left(expires) -> str:
    """Сколько осталось — с округлением вверх.

    «Четырнадцать дней» при оставшихся четырнадцати с половиной читается
    как обман: человек платил за пятнадцать и видит их в панели.
    """
    if not expires:
        return ''
    seconds = (expires - now()).total_seconds()
    if seconds < 0:
        return 'истекла'
    left = -(-int(seconds) // 86400)
    if left <= 1:
        return 'меньше суток'
    return f'{left} дн'


def fields(rows: list[tuple[str, str, str]], *, boxed: bool = True) -> str:
    """Строки вида «значок Подпись: значение» и кнопка справа.

    Таблицей (`boxed`) кнопка встаёт у правого края, а значения — столбиком
    друг под другом: глазу есть за что зацепиться. Второй способ, абзацем
    с <br>, оставлен не для красоты, а на случай отказа: таблицы в Rich
    Message новые, и документация прямо говорит, что в ячейках разрешено
    только строчное оформление — кнопка там может и не пройти. Тогда экран
    уйдёт этим способом, он уже проверен живьём.
    """
    if boxed:
        cells = ''.join(
            f'<tr><td>{label}: {value}</td>'
            f'<td align="right">{action}</td></tr>'
            for label, value, action in rows)
        return f'<table compact>{cells}</table>'

    return '<p>' + '<br>'.join(
        f'{label}: {value}' + (f' {action}' if action else '')
        for label, value, action in rows) + '</p>'


def main_menu(user: dict, *, balance: int = 0, friends: int = 0,
              devices: str = '', email: str = '', support_url: str = '',
              raffle: bool = False, photo: str = '', boxed: bool = True) -> str:
    """HTML главного меню. Ничего не отправляет — только собирает.

    `photo` — ссылка для <img>: `PHOTO_LINK`, если файл уходит списком
    media, или внешний https-адрес. Пусто — экран будет без шапки.
    `boxed` — раскладка данных: таблицей или строками (см. fields).
    """
    card = (user or {}).get('user_data') or {}
    vpn = (user or {}).get('vpn') or {}
    expires = parse_dt(vpn.get('expireAt'))
    has_sub = bool(vpn.get('shortUuid'))

    parts = [image(photo), f'<h3>{e("user")} Профиль</h3>']

    # Те же четыре строки, что на обычном экране профиля: человек узнаёт
    # свой кабинет, а не разбирается в новой вёрстке. Кнопки стоят прямо у
    # своих строк — в обычном боте за ними надо идти вниз, к клавиатуре, и
    # искать среди восьми других. Подписи у них без значков: значок уже
    # стоит слева, у самой строки, и второй рядом только мельтешит.
    parts.append(fields([
        (f'{e("id")} Идентификатор',
         f'<code>{ids.show(card.get("user_id", ""))}</code>', ''),
        (f'{e("money")} Баланс', f'<b>{balance} ₽</b>',
         button('Пополнить', 'payments', 'success')),
        (f'{e("friends")} Друзей', f'<b>{friends}</b>',
         button('Пригласить', 'referrals')),
        (f'{e("email")} Почта',
         f'<code>{escape(email)}</code>' if email else 'не привязана',
         button('Изменить' if email else 'Привязать', 'email')),
    ], boxed=boxed))

    parts.append(f'<h4>{e("shield")} Подписка</h4>')
    if has_sub and expires:
        date = f'<b>{fmt(expires, "%d.%m.%Y")}</b>'
        left = _days_left(expires)
        lines = [(f'{e("calendar")} Истекла' if left == 'истекла'
                  else f'{e("calendar")} Активна до', date, '')]
        if left != 'истекла':
            lines.append((f'{e("hourglass")} Осталось', f'<b>{left}</b>', ''))
        lines.append((f'{e("devices")} Устройства',
                      f'<b>{escape(devices)}</b>' if devices else '—', ''))
        parts.append(fields(lines, boxed=boxed))
        parts.append(row(
            button(f'{e("shield")} Моя подписка', 'my_subscription', 'primary'),
            button(f'{e("renew")} Продлить', 'extend', 'success'),
            button(f'{e("devices")} Устройства', 'devices')))
    else:
        parts.append('<p>Подписки пока нет — подключение занимает минуту, '
                     'а первые настройки бот сделает сам.</p>')
        parts.append(row(
            button(f'{e("plus")} Подключить', 'subscription', 'primary'),
            button(f'{e("gift")} Подарить', 'gifts')))

    # «Пригласить» стоит выше, у строки с друзьями, и второй раз его тут
    # быть не должно: две одинаковые кнопки на экране — это вопрос «а они
    # разные?», а не удобство.
    second = [button(f'{e("gift")} Подарить', 'gifts')] if has_sub else []
    if raffle:
        second.insert(0, button(f'{e("hot")} Розыгрыш', 'raffle'))
    if second:
        parts.append(row(*second[:ROW]))

    # Сворачиваемый блок — то, чего в обычном меню нет вообще: редкие
    # кнопки перестают занимать экран, но остаются в одном нажатии.
    parts.append(
        '<details><summary>Ещё</summary>'
        '<p>Промокод — разовая скидка или подарочные дни. В «О сервисе» — '
        'правила, устройства и ответы на частые вопросы.</p>'
        + row(button(f'{e("promo")} Промокод', 'promo', 'link'),
              button(f'{e("question")} О сервисе', 'about', 'link'),
              align='left')
        + '</details>')

    parts.append('<hr/>')
    footer = f'RS VPN · обновлено {fmt(now(), "%H:%M")}'
    if support_url:
        parts.append(f'<footer>{footer} · '
                     f'<a href="{escape(support_url, quote=True)}">'
                     f'поддержка</a></footer>')
    else:
        parts.append(f'<footer>{footer}</footer>')

    return ''.join(parts)


def showcase() -> str:
    """Всё, что умеет Rich Message, одним экраном — чтобы выбрать нужное."""
    return ''.join([
        '<h3>Что умеет Rich Message</h3>',
        '<p>Обычный текст: <b>жирный</b>, <i>наклонный</i>, '
        '<u>подчёркнутый</u>, <s>зачёркнутый</s>, <mark>выделенный</mark>, '
        '<code>моноширинный</code>, <tg-spoiler>спойлер</tg-spoiler>, '
        'формула <tg-math>x^2 + y^2</tg-math>.</p>',
        '<h4>Таблица</h4>',
        '<table bordered striped compact>'
        '<tr><th>Тариф</th><th>Цена</th><th>Выгода</th></tr>'
        '<tr><td>Месяц</td><td>150 ₽</td><td>—</td></tr>'
        '<tr><td>Полгода</td><td>750 ₽</td><td>−17%</td></tr>'
        '<tr><td>Год</td><td>1400 ₽</td><td>−22%</td></tr>'
        '</table>',
        '<h4>Списки</h4>',
        '<ul><li>обычный пункт</li>'
        '<li><input type="checkbox" checked>сделано</li>'
        '<li><input type="checkbox">ещё нет</li></ul>',
        '<blockquote>Цитата, какой её видит человек.<cite>RS VPN</cite>'
        '</blockquote>',
        '<details><summary>Сворачиваемый блок</summary>'
        '<p>Внутри может быть что угодно, включая кнопки.</p>'
        + row(button('Кнопка внутри', 'profile', 'link'), align='left')
        + '</details>',
        '<h4>Кнопки</h4>',
        '<p>Прямо в тексте: '
        + button('обычная', 'profile') + ' '
        + button('главная', 'payments', 'primary') + ' '
        + button('успех', 'profile', 'success') + ' '
        + button('опасная', 'profile', 'danger') + ' '
        + button('ссылкой', 'profile', 'link')
        + ' и <tg-button type="disabled">недоступная</tg-button>.</p>',
        row(button('Ряд по центру', 'profile', 'primary')),
        '<hr/>',
        '<footer>Это одно текстовое сообщение, не картинка.</footer>',
    ])
