"""Как бот ведёт себя, когда база не отвечает.

Случай не выдуманный: 25 сентября Mongo перестала отвечать, и в логе это
выглядело так — «кнопка dev:list (3303144 мс)», то есть пятьдесят пять
минут на одно нажатие, а следом «query is too old» от Telegram. Человек
за экраном при этом видел ничего: кнопка нажата, ответа нет, ошибки нет.

Отсюда три правила, которые здесь и проверяются: ждать базу не дольше
секунд, отвечать про базу честно и не пытаться записать «база не
отвечает» в ту же самую базу.
"""

import asyncio

import pytest
from aiogram import types

from app.bot.middlewares.errors import (CALLBACK_TIMEOUT_SEC, DB_DOWN, TOO_SLOW,
                                        ErrorsMiddleware, is_db_error)
from app.core import db as names


class FakeMongoError(Exception):
    """Как исключения драйвера: опознаётся по модулю."""
    __module__ = 'pymongo.errors'


from datetime import datetime

CHAT = types.Chat(id=1, type='private')
USER = types.User(id=1, is_bot=False, first_name='Иван', username='u')


def click() -> types.CallbackQuery:
    """Настоящее нажатие: middleware смотрит на тип события."""
    message = types.Message(message_id=1, date=datetime.now(), chat=CHAT,
                            text='экран', from_user=USER)
    return types.CallbackQuery(id='q', from_user=USER, chat_instance='ci',
                               data='dev:list', message=message)


def command(text: str = '/raffle') -> types.Message:
    return types.Message(message_id=2, date=datetime.now(), chat=CHAT,
                         text=text, from_user=USER)


class Recording(ErrorsMiddleware):
    """Тот же middleware, но ответ человеку записывается, а не уходит в сеть."""

    def __init__(self, journal=None):
        super().__init__(type('C', (), {'errors': journal})())
        self.answers: list[str] = []

    async def _reply(self, event, text: str) -> None:
        self.answers.append(text)


class Journal:
    def __init__(self, broken: bool = True):
        self.records: list[dict] = []
        self.broken = broken

    async def record(self, **fields):
        if self.broken:      # журнал ошибок живёт в той же базе
            raise FakeMongoError('timed out')
        self.records.append(fields)


# ── таймауты драйвера ───────────────────────────────────────────────────────
def test_the_driver_is_told_not_to_wait_half_a_minute():
    """Тридцать секунд по умолчанию — это минуты ожидания на одном экране."""
    options = names.client_options()

    assert options['serverSelectionTimeoutMS'] <= 5000
    assert options['connectTimeoutMS'] <= 5000


# ── ответ человеку ──────────────────────────────────────────────────────────
async def test_a_database_error_is_named_as_such():
    async def handler(event, data):
        raise FakeMongoError('195.133.30.98:27017: timed out')

    middleware = Recording()
    await middleware(handler, click(), {})

    assert middleware.answers == [DB_DOWN]


async def test_it_does_not_try_to_write_that_into_the_same_database():
    """«Что-то пошло не так» и попытка записать это в лежащую базу — худший
    из возможных ответов: ошибки нет ни на экране, ни в журнале."""
    journal = Journal()

    async def handler(event, data):
        raise FakeMongoError('timed out')

    middleware = Recording(journal)
    await middleware(handler, click(), {})

    assert journal.records == []
    assert middleware.answers == [DB_DOWN]


async def test_an_ordinary_crash_still_goes_to_the_journal():
    journal = Journal(broken=False)

    async def handler(event, data):
        raise ValueError('обычная поломка')

    middleware = Recording(journal)
    await middleware(handler, click(), {})

    assert len(journal.records) == 1
    assert middleware.answers and middleware.answers[0] != DB_DOWN


# ── потолок на нажатие ──────────────────────────────────────────────────────
async def test_a_button_that_hangs_gets_an_honest_answer(monkeypatch):
    """Иначе нажатие пропадает совсем: Telegram отвечает «query is too old»,
    а человек так и не узнаёт, что произошло."""
    monkeypatch.setattr('app.bot.middlewares.errors.CALLBACK_TIMEOUT_SEC', 0.05)

    async def handler(event, data):
        await asyncio.sleep(5)

    middleware = Recording()
    await middleware(handler, click(), {})

    assert middleware.answers == [TOO_SLOW]


async def test_the_ceiling_is_measured_in_seconds_not_minutes():
    assert 5 <= CALLBACK_TIMEOUT_SEC <= 60


async def test_commands_are_not_cut_short(monkeypatch):
    """Админские отчёты идут по всему журналу и законно считаются долго."""
    monkeypatch.setattr('app.bot.middlewares.errors.CALLBACK_TIMEOUT_SEC', 0.01)
    done = []

    async def handler(event, data):
        await asyncio.sleep(0.05)
        done.append(True)

    await Recording()(handler, command(), {})

    assert done == [True]


# ── опознание ошибки ────────────────────────────────────────────────────────
def test_a_driver_error_is_told_from_a_normal_one():
    assert is_db_error(FakeMongoError('x')) is True
    assert is_db_error(ValueError('x')) is False
