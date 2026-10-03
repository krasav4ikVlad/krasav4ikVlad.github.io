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


def action(text: str, data: str, style: str = '') -> str:
    """Кнопка с любым callback-данными: Menu, Payment и прочие фабрики."""
    styled = f' style="{style}"' if style else ''
    return (f'<tg-button type="callback_data"{styled} '
            f'data="{escape(data, quote=True)}">{text}</tg-button>')


def button(text: str, screen: str, style: str = '') -> str:
    """Кнопка, ведущая в тот же раздел, что и обычная клавиатура."""
    return action(text, Menu(screen=screen).pack(), style)


def link(text: str, url: str, style: str = '') -> str:
    """Кнопка наружу: мини-апп оплаты, страница подключения."""
    styled = f' style="{style}"' if style else ''
    return (f'<tg-button type="url"{styled} '
            f'url="{escape(url, quote=True)}">{text}</tg-button>')


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

    parts.append(tail(support_url))
    return ''.join(parts)


def tail(support_url: str = '') -> str:
    """Подвал, одинаковый на всех экранах примерки."""
    line = f'RS VPN · обновлено {fmt(now(), "%H:%M")}'
    if support_url:
        line += (f' · <a href="{escape(support_url, quote=True)}">'
                 f'поддержка</a>')
    return f'<hr/><footer>{line}</footer>'


# Инструкция в сворачиваемом блоке: на обычном экране её нет вовсе — она
# не влезала, и человек уходил за ней на страницу подключения. Текст
# короткий нарочно: подробности всё равно живут на той странице, а здесь
# нужно снять первый страх «а что дальше-то делать».
SETUP = (
    ('iPhone и iPad', 'Установите <b>Happ</b> из App Store, откройте ссылку '
                      'выше — приложение подхватит подписку само.'),
    ('Android', 'Установите <b>Happ</b> из Google Play и откройте ссылку '
                'выше. Если спросит «чем открыть» — выберите Happ.'),
    ('Компьютер', 'Happ есть для Windows и macOS. Скопируйте ссылку выше '
                  'и вставьте её в приложении: «Добавить подписку».'),
)


def my_subscription(user: dict, *, devices: int = 0, price: str = '',
                    devices_fee: str = '', connect: str = '',
                    support_url: str = '', photo: str = '',
                    boxed: bool = True) -> str:
    """Экран действующей подписки.

    Отличий от обычного три, и все три — про то, чего в обычном не хватало:
    даты, устройства и цены стоят таблицей, ссылка подключения — отдельной
    строкой моноширинным (её копируют в один тап, а не выделяют пальцем из
    переносов), инструкция убрана в сворачиваемый блок.
    """
    vpn = (user or {}).get('vpn') or {}
    expires = parse_dt(vpn.get('expireAt'))
    left = _days_left(expires)

    parts = [image(photo), f'<h3>{e("shield")} Моя подписка</h3>']

    rows = []
    if expires:
        rows.append((f'{e("calendar")} Истекла' if left == 'истекла'
                     else f'{e("calendar")} Действует до',
                     f'<b>{fmt(expires, "%d.%m.%Y")}</b>',
                     button('Изменить длительность', 'period')))
        if left and left != 'истекла':
            rows.append((f'{e("hourglass")} Осталось', f'<b>{left}</b>', ''))
    rows.append((f'{e("devices")} Лимит устройств', f'<b>{int(devices or 0)}</b>',
                 button('Менеджер устройств', 'devices')))
    if price:
        rows.append((f'{e("payout")} Плата за подписку', price, ''))
    if devices_fee:
        rows.append((f'{e("devices")} Плата за устройства', devices_fee, ''))
    parts.append(fields(rows, boxed=boxed))

    if connect:
        # Моноширинным и отдельным абзацем: в Telegram такой текст
        # копируется одним нажатием, а ссылка в тексте — выделением.
        parts.append(f'<p>{e("link")} Ссылка на подключение — нажмите, '
                     f'чтобы скопировать:</p>'
                     f'<p><code>{escape(connect)}</code></p>')
        parts.append(row(link(f'{e("shield")} Настроить VPN', connect, 'primary'),
                         button(f'{e("renew")} Продлить', 'extend', 'success')))
    else:
        parts.append(row(button(f'{e("plus")} Подключить', 'subscription',
                                'primary')))

    parts.append(
        '<details><summary>Как подключить: iPhone, Android, ПК</summary>'
        + ''.join(f'<p><b>{title}</b><br>{text}</p>' for title, text in SETUP)
        + '</details>')

    parts.append(row(button(f'{e("back")} В профиль', 'profile')))
    parts.append(tail(support_url))
    return ''.join(parts)


FAQ = (
    ('Не пришли деньги?',
     'Зачисление идёт по уведомлению банка: обычно это секунды, но в час '
     'пик бывает до получаса. Если прошло больше — напишите в поддержку '
     'и приложите чек, деньги найдутся по нему.'),
    ('Как оплатить криптой?',
     'Выберите «Криптовалюта»: бот выдаст адрес и сумму. Баланс пополнится '
     'после подтверждения перевода сетью — это не мгновенно, зависит от '
     'монеты и загрузки сети.'),
)


def topup(user: dict, *, balance: int = 0, price: str = '',
          methods: list[dict] | None = None, bonus: str = '',
          tribute_bonus: str = '', support_url: str = '', photo: str = '',
          boxed: bool = True) -> str:
    """Экран пополнения: способы таблицей, у каждого — своя кнопка.

    `methods` — по словарю на способ: title, fee, speed, button (готовый
    html кнопки). Бонусы приходят строками: считает их не экран, а
    TopupService, и выдумывать проценты здесь нельзя.
    """
    parts = [image(photo), f'<h3>{e("money")} Пополнение баланса</h3>']

    head = [(f'{e("money")} На балансе', f'<b>{balance} ₽</b>', '')]
    if price:
        head.append((f'{e("payout")} Плата за подписку', price, ''))
    parts.append(fields(head, boxed=boxed))

    # Маркером, а не цитатой: цитата на экране означает «пояснение
    # мелким шрифтом», а это, наоборот, то, ради чего стоит выбрать
    # способ подороже.
    for note in (bonus, tribute_bonus):
        if note:
            parts.append(f'<p><mark>{note}</mark></p>')

    parts.append(methods_table(methods or [], boxed=boxed))

    parts.append(
        '<details><summary>Частые вопросы</summary>'
        + ''.join(f'<p><b>{title}</b><br>{text}</p>' for title, text in FAQ)
        + '</details>')

    parts.append(row(button(f'{e("back")} В профиль', 'profile')))
    parts.append(tail(support_url))
    return ''.join(parts)


def methods_table(methods: list[dict], *, boxed: bool = True) -> str:
    """Способы оплаты: что берут сверху, когда зачислят и кнопка оплаты."""
    if not methods:
        return (f'<p>{e("cross")} Ни один способ оплаты сейчас не включён. '
                f'Напишите в поддержку — пополним вручную.</p>')

    if boxed:
        head = ('<tr><th>Способ</th><th>Комиссия</th><th>Зачисление</th>'
                '<th></th></tr>')
        body = ''.join(
            f'<tr><td>{m["title"]}</td><td>{escape(str(m.get("fee") or ""))}</td>'
            f'<td>{escape(str(m.get("speed") or ""))}</td>'
            f'<td align="right">{m.get("button") or ""}</td></tr>'
            for m in methods)
        return f'<table compact>{head}{body}</table>'

    return '<p>' + '<br>'.join(
        f'{m["title"]} — комиссия {escape(str(m.get("fee") or ""))}, '
        f'{escape(str(m.get("speed") or ""))} {m.get("button") or ""}'
        for m in methods) + '</p>'


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
