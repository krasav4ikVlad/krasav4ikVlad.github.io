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

from app.bot.callbacks import Menu
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


def test_the_top_up_button_stands_next_to_the_balance():
    """Кнопка должна быть в том же абзаце, что баланс, а не ниже экрана."""
    html = rich_menu.main_menu(user(), balance=270)

    paragraph = next(p for p in re.findall(r'<p>(.*?)</p>', html)
                     if 'Баланс' in p)

    assert Menu(screen='payments').pack() in paragraph


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

    assert 'осталось 15 дн' in html


def test_an_expired_subscription_says_so():
    card = user()
    card['vpn']['expireAt'] = now() - timedelta(days=2)

    assert 'истекла' in rich_menu.main_menu(card)


def test_someones_name_cannot_break_the_markup():
    """Имя приходит от человека, а разметка — наша. Пересекаться им нельзя."""
    card = user()
    card['user_data']['first_name'] = '<b>хитрый</b>'

    html = rich_menu.main_menu(card, email='<script>x</script>@mail.ru')

    assert '<b>хитрый</b>' not in html and '&lt;b&gt;' in html
    assert '<script>' not in html


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

    `broken` — отказывать всегда, `picky` — только сообщениям с картинкой:
    так ведёт себя Telegram, которому не понравилась именно она.
    """

    def __init__(self, broken: str = '', picky: str = ''):
        self.sent: list[str] = []
        self.media: list[list] = []
        self.broken = broken
        self.picky = picky

    async def send_rich_message(self, chat_id, rich_message, **kwargs):
        if self.broken:
            raise RuntimeError(self.broken)
        if self.picky and rich_message.media:
            raise RuntimeError(self.picky)
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
