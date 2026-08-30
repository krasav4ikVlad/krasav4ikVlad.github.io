"""Журнал ошибок: что человек увидел и что случилось на самом деле.

«Сервис подписок не отвечает» — честный текст для человека и бесполезный
для того, кто чинит. Кто именно, на каком экране и что ответила панель,
раньше уходило строкой INFO в лог на сервере, без имени пользователя.
"""

from datetime import datetime, timedelta

import pytest
from aiogram import types

from app.bot.middlewares.errors import ErrorsMiddleware
from app.core.errors import NotEnoughBalance, VpnPanelError
from app.core.time import now
from app.repositories.errors import ErrorLogRepository


class Container:
    def __init__(self, journal):
        self.errors = journal


def Call(data='m:profile:', user_id=802421217, username='vlad'):
    """Настоящий CallbackQuery: middleware разбирает событие по типу, и на
    подделке ветка «откуда пришло» просто не сработала бы."""
    user = types.User(id=user_id, is_bot=False, first_name='Влад',
                      username=username)
    chat = types.Chat(id=user_id, type='private')
    return types.CallbackQuery(
        id='q', from_user=user, chat_instance='ci', data=data,
        message=types.Message(message_id=1, date=datetime.now(), chat=chat,
                              from_user=user, text='экран'))


@pytest.fixture
def journal(db):
    return ErrorLogRepository(db['bot_errors'])


async def run(journal, event, exc):
    """Прогнать событие через middleware, где хендлер падает.

    Ответ пользователю здесь не проверить: у события нет бота, и отправка
    гасится собственным try/except. Проверяем то, ради чего всё затевалось, —
    запись в журнале.
    """
    async def handler(event, data):
        raise exc

    middleware = ErrorsMiddleware(Container(journal))
    await middleware(handler, event, {})


async def test_a_panel_error_keeps_the_real_answer(db, journal):
    """Код и тело ответа Remnawave — то единственное, по чему видно причину."""
    await run(journal, Call(),
              VpnPanelError('PATCH /api/users: HTTP 400 hwidDeviceLimit invalid'))

    row = (await journal.recent())[0]
    assert row['kind'] == 'panel'
    assert 'HTTP 400' in row['message'] and 'hwidDeviceLimit' in row['message']
    assert row['shown'] == VpnPanelError.user_message


async def test_it_remembers_who_and_where(db, journal):
    await run(journal, Call(data='dev:buy:2', user_id=42, username='petya'),
              VpnPanelError('PATCH /api/users: HTTP 500'))

    row = (await journal.recent())[0]
    assert row['user_id'] == 42 and row['username'] == 'petya'
    assert row['where'] == 'dev:buy:2', 'экран не записан — искать негде'


async def test_expected_errors_are_marked_apart_from_crashes(db, journal):
    """«Не хватает денег» и «упало» — разные строки в отчёте."""
    await run(journal, Call(), NotEnoughBalance(need=100, have=10))
    await run(journal, Call(), ValueError('что-то пошло не так'))

    kinds = [row['kind'] for row in await journal.recent()]
    assert kinds == ['crash', 'app']


async def test_errors_can_be_filtered_by_user_and_kind(db, journal):
    await run(journal, Call(user_id=1), VpnPanelError('HTTP 500'))
    await run(journal, Call(user_id=2), VpnPanelError('HTTP 500'))
    await run(journal, Call(user_id=2), ValueError('сбой'))

    assert len(await journal.recent(user_id=2)) == 2
    assert len(await journal.recent(kind='panel')) == 2


async def test_the_count_for_the_day_is_what_diag_shows(db, journal):
    await run(journal, Call(), VpnPanelError('HTTP 500'))

    day = now() - timedelta(days=1)
    assert await journal.count_since(day) == 1
    assert await journal.count_since(day, kind='panel') == 1
    assert await journal.count_since(now() + timedelta(minutes=1)) == 0


async def test_a_broken_journal_does_not_break_anything(db):
    """Журнал не должен мешать: обработчик и так уже упал, а человеку нужен
    ответ, а не вторая ошибка поверх первой."""

    class Broken:
        async def record(self, **kw):
            raise RuntimeError('база недоступна')

    await run(Broken(), Call(), VpnPanelError('HTTP 500'))   # не должно упасть


async def test_without_a_journal_the_middleware_still_works(db):
    """Контейнер без журнала — обычное дело в тестах и на старте."""

    async def handler(event, data):
        raise VpnPanelError('HTTP 500')

    await ErrorsMiddleware(None)(handler, Call(), {})
