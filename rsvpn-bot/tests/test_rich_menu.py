"""Главное меню в виде Rich Message.

Rich Messages — Bot API 10.1, кнопки внутри текста — 10.3. Это не картинка
и не Mini App: сообщение остаётся текстом, но внутри заголовки, картинка,
сворачиваемый блок и кнопки прямо в потоке.

Проверять тут нечего, кроме одного: кнопки должны вести туда же, куда
обычное меню, иначе красивый экран окажется неработающим. И ещё двух
мелочей, которые ломаются молча: картинка живёт отдельным списком media
(в HTML от неё только ссылка), а кастомные эмодзи навешиваются вручную —
middleware сессии до sendRichMessage не достаёт.
"""

import re

import pytest

from app.bot.callbacks import Menu, Payment
from app.bot.screens import rich_menu
from app.core.time import now

from datetime import timedelta


def user(sub: bool = True) -> dict:
    card = {'user_data': {'user_id': 802421217, 'first_name': 'Иван'},
            'info': {'balance': 1500}}
    if sub:
        card['vpn'] = {'shortUuid': 's-1', 'expireAt': now() + timedelta(days=15)}
    return card


def buttons(html: str) -> list[str]:
    """Что отправится боту при нажатии — ровно то, что в data."""
    return re.findall(r'data="([^"]+)"', html)


# ── кнопки ведут туда же, куда обычное меню ─────────────────────────────────
def test_the_buttons_lead_where_the_usual_menu_leads():
    html = rich_menu.main_menu(user(), balance=100)

    assert Menu(screen='payments').pack() in buttons(html)
    assert Menu(screen='my_subscription').pack() in buttons(html)


def test_a_person_without_a_subscription_is_offered_one():
    html = rich_menu.main_menu(user(sub=False))

    assert Menu(screen='subscription').pack() in buttons(html)
    assert 'Подписки пока нет' in html


def test_the_subscription_block_has_its_own_buttons():
    """Продлить и устройства — рядом с датой, а не в конце экрана."""
    html = rich_menu.main_menu(user())

    assert Menu(screen='extend').pack() in buttons(html)
    assert Menu(screen='devices').pack() in buttons(html)


def test_the_raffle_button_appears_only_during_the_raffle():
    with_raffle = rich_menu.main_menu(user(), raffle=True)
    without = rich_menu.main_menu(user(), raffle=False)

    assert Menu(screen='raffle').pack() in buttons(with_raffle)
    assert Menu(screen='raffle').pack() not in buttons(without)


def test_no_row_is_longer_than_the_screen():
    """Восемь кнопок в ряд разрешает API, но читаются три."""
    html = rich_menu.main_menu(user(), raffle=True)

    for row in re.findall(r'<tg-button-row[^>]*>(.*?)</tg-button-row>', html):
        assert row.count('<tg-button') <= rich_menu.ROW


# ── данные ──────────────────────────────────────────────────────────────────
def test_the_numbers_are_shown():
    html = rich_menu.main_menu(user(), balance=1500, friends=7,
                               devices='до 3')

    assert '1500 ₽' in html and '>7<' in html and 'до 3' in html


def test_the_usual_four_lines_are_in_place():
    """Те же подписи, что на обычном экране профиля: кабинет узнаваем."""
    html = rich_menu.main_menu(user(), balance=270, friends=38,
                               email='i@rseeed.ru')

    for line in ('Идентификатор', 'Баланс', 'Друзей', 'Почта'):
        assert line in html, line
    assert '802421217' in html and 'i@rseeed.ru' in html


def rows(html: str) -> dict[str, str]:
    """Строки таблицы по подписи: «Баланс» → содержимое всей строки."""
    found = {}
    for cells in re.findall(r'<tr>(.*?)</tr>', html):
        label = re.sub(r'<[^>]+>', '', cells).split(':')[0].strip()
        found[label.split(' ')[-1]] = cells
    return found


def test_each_line_has_its_own_button():
    """Кнопка стоит у своей строки, а не внизу экрана среди восьми других."""
    html = rich_menu.main_menu(user(), balance=270, friends=38,
                               email='i@rseeed.ru')
    line = rows(html)

    assert Menu(screen='payments').pack() in line['Баланс']
    assert Menu(screen='referrals').pack() in line['Друзей']
    assert Menu(screen='email').pack() in line['Почта']


def test_those_buttons_stand_at_the_right_edge():
    """Значение слева, кнопка справа — иначе строки не читаются столбиком."""
    html = rich_menu.main_menu(user(), balance=270)

    assert f'<td align="right">{rich_menu.button("Пополнить", "payments", "success")}' \
        in rows(html)['Баланс']


def test_those_buttons_are_without_signs():
    """Значок уже стоит слева, у самой строки: второй рядом только мельтешит."""
    line = rows(rich_menu.main_menu(user(), email='i@rseeed.ru'))

    for label in ('Баланс', 'Друзей', 'Почта'):
        caption = re.search(r'<tg-button[^>]*>(.*?)</tg-button>', line[label])
        assert caption and caption.group(1).isalpha(), label


def test_a_person_without_email_is_offered_to_add_one():
    html = rich_menu.main_menu(user(), email='')

    assert 'Привязать' in html and 'не привязана' in html


def test_the_same_data_can_be_laid_out_in_lines():
    """Запасная раскладка на случай, если таблицу Telegram не примет."""
    html = rich_menu.main_menu(user(), balance=270, friends=38,
                               email='i@rseeed.ru', boxed=False)

    assert '<table' not in html and '<br>' in html
    for screen in ('payments', 'referrals', 'email'):
        assert Menu(screen=screen).pack() in buttons(html)


def test_the_picture_is_a_block_of_its_own():
    """Картинку внутри абзаца Telegram не примет — только отдельным блоком."""
    html = rich_menu.main_menu(user(), photo=rich_menu.PHOTO_LINK)

    assert html.startswith(f'<img src="{rich_menu.PHOTO_LINK}"/>')
    assert '<p>' not in html[:html.index('/>')]


def test_without_a_picture_there_is_no_empty_tag():
    html = rich_menu.main_menu(user())

    assert '<img' not in html


def test_the_days_left_are_counted():
    html = rich_menu.main_menu(user())

    assert 'Осталось' in html and '15 дн' in html


def test_an_expired_subscription_says_so():
    """«Осталось: истекла» не по-русски: у истёкшей меняется подпись даты."""
    card = user()
    card['vpn']['expireAt'] = now() - timedelta(days=2)

    html = rich_menu.main_menu(card)

    assert 'Истекла' in html and 'Осталось' not in html


def test_what_a_person_typed_cannot_break_the_markup():
    """Почту человек вписывает сам, а разметка — наша. Пересекаться нельзя."""
    html = rich_menu.main_menu(user(), email='<script>x</script>@mail.ru',
                               devices='<b>до 3</b>')

    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<b>до 3</b>' not in html


# ── экран подписки ──────────────────────────────────────────────────────────
#
# Три вещи, ради которых экран переделан: таблица вместо строк, ссылка
# моноширинным (её копируют в один тап) и инструкция в сворачиваемом блоке.
# Кнопки стоят у своих строк: «Менеджер устройств» — у лимита, «Изменить
# длительность» — у даты. Перепутать их местами легко, заметить трудно.
LINK = 'https://connect.rsvps.tech/abcDEF123'


def test_the_subscription_buttons_stand_by_their_own_lines():
    html = rich_menu.my_subscription(user(), devices=10, connect=LINK)
    line = rows(html)

    assert Menu(screen='devices').pack() in line['устройств']
    assert Menu(screen='period').pack() in line['до']


def test_the_connect_link_is_one_tap_to_copy():
    """Ссылка длинная и переносится: моноширинным её копируют нажатием."""
    html = rich_menu.my_subscription(user(), connect=LINK)

    assert f'<code>{LINK}</code>' in html


def test_the_setup_notes_are_folded_away():
    html = rich_menu.my_subscription(user(), connect=LINK)

    block = re.search(r'<details><summary>(.*?)</summary>(.*?)</details>', html)
    assert block and 'подключить' in block.group(1)
    for platform in ('iPhone', 'Android', 'Компьютер'):
        assert platform in block.group(2), platform


def test_the_subscription_shows_both_prices():
    html = rich_menu.my_subscription(user(), devices=10, price='150₽ за месяц',
                                     devices_fee='225₽ в месяц')

    assert '150₽ за месяц' in html and '225₽ в месяц' in html


def test_a_person_without_a_link_is_offered_to_connect():
    html = rich_menu.my_subscription(user(sub=False), connect='')

    assert '<code>' not in html
    assert Menu(screen='subscription').pack() in buttons(html)


# ── экран пополнения ────────────────────────────────────────────────────────
METHODS = [
    {'title': '⚡️ СБП', 'fee': '+5% сверху', 'speed': 'обычно сразу',
     'button': rich_menu.action('Оплатить', 'pay:wata', 'success')},
    {'title': '💸 Криптовалюта', 'fee': 'комиссия сети',
     'speed': 'после подтверждения сети',
     'button': rich_menu.link('Оплатить', 'https://pay.example/1', 'success')},
]


def test_every_payment_method_is_a_row_with_its_own_button():
    html = rich_menu.topup(user(), balance=270, methods=METHODS)

    assert '<th>Комиссия</th>' in html and '<th>Зачисление</th>' in html
    sbp = next(r for r in re.findall(r'<tr>(.*?)</tr>', html) if 'СБП' in r)
    crypto = next(r for r in re.findall(r'<tr>(.*?)</tr>', html)
                  if 'Криптовалюта' in r)

    assert '+5% сверху' in sbp and 'pay:wata' in sbp
    assert 'комиссия сети' in crypto and 'https://pay.example/1' in crypto


def test_the_bonus_is_marked_not_quoted():
    """Цитата читается как сноска мелким шрифтом, а это — повод выбрать."""
    html = rich_menu.topup(user(), methods=METHODS, bonus='+10% сверху',
                           tribute_bonus='Tribute — ещё +5%')

    assert '<mark>+10% сверху</mark>' in html
    assert '<mark>Tribute — ещё +5%</mark>' in html
    assert '<blockquote>' not in html


def test_the_questions_are_folded_away():
    html = rich_menu.topup(user(), methods=METHODS)

    block = re.search(r'<details><summary>(.*?)</summary>(.*?)</details>', html)
    assert block and 'Не пришли деньги?' in block.group(2)
    assert 'криптой' in block.group(2)


def test_a_screen_without_any_method_does_not_pretend():
    """Пустая таблица выглядит как поломка; человеку нужен выход."""
    html = rich_menu.topup(user(), methods=[])

    assert '<table' not in html.split('<details>')[0].split('</table>')[-1]
    assert 'поддержку' in html


def test_the_payment_rows_can_be_laid_out_in_lines():
    html = rich_menu.topup(user(), methods=METHODS, boxed=False)

    assert '<table' not in html and 'pay:wata' in html
    assert 'комиссия сети' in html


# ── витрина возможностей ────────────────────────────────────────────────────
def test_the_showcase_has_every_element_worth_choosing():
    html = rich_menu.showcase()

    for tag in ('<table', '<details', '<blockquote', '<ul>', '<hr/>',
                '<footer>', '<tg-button', '<tg-spoiler>', '<h3>'):
        assert tag in html, tag


def test_the_showcase_shows_all_button_styles():
    html = rich_menu.showcase()

    for style in ('primary', 'success', 'danger', 'link'):
        assert f'style="{style}"' in html
    assert 'type="disabled"' in html


# ── команда ─────────────────────────────────────────────────────────────────
#
# Примерка не должна молчать: если Telegram или aiogram не умеют Rich
# Message, человек обязан увидеть ответ API словами — ровно это и надо
# выяснить до того, как переводить на такой вид весь бот.

from tests.test_admin_panel import ADMIN, admin_env, message  # noqa: E402, F401


class Spy:
    """Бот, запоминающий, что ему передали вместо отправки.

    `broken` — отказывать всегда, `picky` — сообщениям с картинкой,
    `fussy` — сообщениям с таблицей: так ведёт себя Telegram, которому не
    понравилась одна конкретная часть экрана.
    """

    def __init__(self, broken: str = '', picky: str = '', fussy: str = ''):
        self.sent: list[str] = []
        self.media: list[list] = []
        self.broken = broken
        self.picky = picky
        self.fussy = fussy

    async def send_rich_message(self, chat_id, rich_message, **kwargs):
        if self.broken:
            raise RuntimeError(self.broken)
        if self.picky and rich_message.media:
            raise RuntimeError(self.picky)
        if self.fussy and '<table' in rich_message.html:
            raise RuntimeError(self.fussy)
        self.sent.append(rich_message.html)
        self.media.append(list(rich_message.media or []))
        return True


class FakePhoto:
    """Картинка экрана: отдаёт готовый file_id, как настоящая из кэша."""

    def as_input(self):
        return 'file-id-1'


async def test_the_command_sends_a_rich_message(admin_env, monkeypatch):
    dp, bot, session, c = admin_env
    await c.users.create({'user_data': {'user_id': ADMIN.id,
                                        'first_name': 'Влад'},
                          'info': {'balance': 500}})
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich'))

    assert spy.sent and '<tg-button' in spy.sent[0]
    assert 'Профиль' in spy.sent[0]


async def test_the_subscription_screen_is_asked_for_by_word(admin_env,
                                                            monkeypatch):
    dp, bot, session, c = admin_env
    await c.users.create({'user_data': {'user_id': ADMIN.id},
                          'vpn': {'shortUuid': 'abc', 'hwidDeviceLimit': 10,
                                  'expireAt': now() + timedelta(days=30)}})
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich подписка'))

    assert 'Моя подписка' in spy.sent[0]
    assert '<code>' in spy.sent[0] and 'abc</code>' in spy.sent[0]
    assert Menu(screen='devices').pack() in buttons(spy.sent[0])


async def test_the_topup_screen_lists_the_working_providers(admin_env,
                                                            monkeypatch):
    """Способы берутся из реестра: выключенный в админке не должен всплыть."""
    dp, bot, session, c = admin_env

    class Provider:
        code, title = 'wata', '⚡️ СБП'
        fee, speed, direct_url = '+5% сверху', 'обычно сразу', ''

    class Registry:
        async def available(self):
            return [Provider()]

    monkeypatch.setattr(c, 'payments', Registry())
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich pay'))

    assert 'Пополнение баланса' in spy.sent[0]
    assert 'СБП' in spy.sent[0] and '+5% сверху' in spy.sent[0]
    assert Payment(provider='wata').pack() in buttons(spy.sent[0])


async def test_the_showcase_is_asked_for_by_word(admin_env, monkeypatch):
    dp, bot, session, c = admin_env
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich all'))

    assert spy.sent and '<table' in spy.sent[0]


async def test_the_picture_goes_in_the_media_list(admin_env, monkeypatch):
    """В HTML — только ссылка tg://photo?id=; сам файл едет отдельно."""
    dp, bot, session, c = admin_env
    await c.users.create({'user_data': {'user_id': ADMIN.id}, 'info': {}})
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)
    monkeypatch.setattr(c, 'media', lambda key: FakePhoto())

    await dp.feed_update(bot, message('/rich'))

    assert rich_menu.PHOTO_LINK in spy.sent[0]
    attached = spy.media[0]
    assert [m.id for m in attached] == [rich_menu.PHOTO]
    assert attached[0].media.media == 'file-id-1'


async def test_without_a_picture_nothing_is_attached(admin_env, monkeypatch):
    dp, bot, session, c = admin_env
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)
    monkeypatch.setattr(c, 'media', lambda key: None)

    await dp.feed_update(bot, message('/rich'))

    assert spy.media[0] == [] and 'tg://photo' not in spy.sent[0]


async def test_the_layout_survives_a_refused_picture(admin_env, monkeypatch):
    """Картинка — не повод не увидеть экран: вторая попытка без неё."""
    dp, bot, session, c = admin_env
    spy = Spy(picky='MEDIA_INVALID')
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)
    monkeypatch.setattr(c, 'media', lambda key: FakePhoto())

    await dp.feed_update(bot, message('/rich'))

    assert spy.sent and '<img' not in spy.sent[0]
    assert 'Профиль' in spy.sent[0]
    assert 'артинку' in session.last_text


async def test_the_layout_survives_a_refused_table(admin_env, monkeypatch):
    """Кнопка в ячейке документацией не обещана — на этот случай есть строки."""
    dp, bot, session, c = admin_env
    await c.users.create({'user_data': {'user_id': ADMIN.id}, 'info': {}})
    spy = Spy(fussy='TABLE_CELL_INVALID')
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)
    monkeypatch.setattr(c, 'media', lambda key: None)

    await dp.feed_update(bot, message('/rich'))

    assert spy.sent and '<table' not in spy.sent[0] and '<br>' in spy.sent[0]
    assert Menu(screen='payments').pack() in buttons(spy.sent[0])
    assert 'аблицу' in session.last_text


async def test_the_brand_emoji_are_put_in_by_hand(admin_env, monkeypatch):
    """У sendRichMessage нет parse_mode, и middleware его не оформляет."""
    dp, bot, session, c = admin_env
    spy = Spy()
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich'))

    assert '<tg-emoji emoji-id=' in spy.sent[0]


async def test_a_refusal_is_shown_in_words(admin_env, monkeypatch):
    """«Method not found» должно оказаться на экране, а не в логе."""
    dp, bot, session, c = admin_env
    spy = Spy(broken='method not found')
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich'))

    assert 'method not found' in session.last_text
    assert 'aiogram' in session.last_text
