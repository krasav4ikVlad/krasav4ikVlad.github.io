"""Главное меню в виде Rich Message.

Rich Messages — Bot API 10.1, кнопки внутри текста — 10.3. Это не картинка
и не Mini App: сообщение остаётся текстом, но внутри заголовки, таблица,
сворачиваемый блок и кнопки прямо в потоке.

Проверять тут нечего, кроме одного: кнопки должны вести туда же, куда
обычное меню, иначе красивый экран окажется неработающим.
"""

import re

import pytest

from app.bot.callbacks import Menu
from app.bot.screens import rich_menu
from app.core.time import now

from datetime import timedelta


def user(sub: bool = True) -> dict:
    card = {'user_data': {'user_id': 1, 'first_name': 'Иван'},
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
    assert 'подписки пока нет' in html


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
    """Бот, запоминающий, что ему передали вместо отправки."""

    def __init__(self, broken: str = ''):
        self.sent: list[str] = []
        self.broken = broken

    async def send_rich_message(self, chat_id, rich_message, **kwargs):
        if self.broken:
            raise RuntimeError(self.broken)
        self.sent.append(rich_message.html)
        return True


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


async def test_a_refusal_is_shown_in_words(admin_env, monkeypatch):
    """«Method not found» должно оказаться на экране, а не в логе."""
    dp, bot, session, c = admin_env
    spy = Spy(broken='method not found')
    monkeypatch.setattr(type(bot), 'send_rich_message', spy.send_rich_message,
                        raising=False)

    await dp.feed_update(bot, message('/rich'))

    assert 'method not found' in session.last_text
    assert 'aiogram' in session.last_text
