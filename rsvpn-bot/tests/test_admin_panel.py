"""Админка целиком: реальный Dispatcher aiogram, но без сети и без Mongo."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import panel
from app.bot.callbacks import Admin as Adm
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.user import UserMiddleware

CHAT = Chat(id=1, type='private')
ADMIN = User(id=1, is_bot=False, first_name='Admin')


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, getattr(method, 'text', '') or ''))
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=999, date=datetime.now(), chat=CHAT,
                       text=getattr(method, 'text', '') or '', from_user=ADMIN)


@pytest.fixture
async def admin_env(container):
    from app.admin.entities import build_entities

    await container.startup()
    container.entities = build_entities(container)

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)

    dp = Dispatcher()
    for middleware in (DependenciesMiddleware(container), UserMiddleware(container.users)):
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    dp.include_router(panel.create_router(container.config.admin_ids))

    yield dp, bot, session, container
    await bot.session.close()


def callback(data: str) -> Update:
    message = Message(message_id=10, date=datetime.now(), chat=CHAT, text='panel',
                      from_user=ADMIN)
    return Update(update_id=1, callback_query=CallbackQuery(
        id='q', from_user=ADMIN, chat_instance='ci', data=data, message=message))


def message(text: str) -> Update:
    return Update(update_id=2, message=Message(
        message_id=11, date=datetime.now(), chat=CHAT, text=text, from_user=ADMIN))


async def test_admin_opens_and_shows_stats(admin_env):
    dp, bot, session, _ = admin_env
    await dp.feed_update(bot, message('/admin'))
    assert 'Админ-панель' in session.calls[-1][1]


async def test_toggle_feature_from_panel(admin_env):
    dp, bot, session, container = admin_env

    assert await container.settings.flag('features.extend_enabled') is True
    await dp.feed_update(bot, callback(Adm(act='tgl', a='features.extend_enabled').pack()))
    assert await container.settings.flag('features.extend_enabled') is False


async def test_change_plan_price_from_panel(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='eedit', a='plan|price', b='1month').pack()))
    await dp.feed_update(bot, message('199'))

    plan = await container.plans.get('1month')
    assert plan['price'] == 199


async def test_bad_value_is_rejected(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='fld', a='pay.min_topup').pack()))
    await dp.feed_update(bot, message('не число'))

    assert 'целое число' in session.calls[-1][1]
    assert await container.settings.int('pay.min_topup') == 75


async def test_non_admin_is_ignored(admin_env):
    dp, bot, session, container = admin_env
    stranger = User(id=999, is_bot=False, first_name='Stranger')
    update = Update(update_id=3, message=Message(
        message_id=12, date=datetime.now(), chat=Chat(id=999, type='private'),
        text='/admin', from_user=stranger))

    before = len(session.calls)
    await dp.feed_update(bot, update)
    assert len(session.calls) == before
