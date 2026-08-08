"""Админка целиком: реальный Dispatcher aiogram, но без сети и без Mongo."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import panel
from app.bot.callbacks import Admin as Adm
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.emoji import PlainEmojiMiddleware, emoji_middleware
from app.bot.middlewares.user import UserMiddleware
from app.content.emoji import BY_CHAR, e

CHAT = Chat(id=1, type='private')
ADMIN = User(id=1, is_bot=False, first_name='Admin')


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.markups: list = []
        self.all_markups: list = []      # markups обход чистит, этот — нет

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, getattr(method, 'text', None)
                           or getattr(method, 'caption', None) or ''))
        if getattr(method, 'reply_markup', None) is not None:
            self.markups.append(method.reply_markup)
            self.all_markups.append(method.reply_markup)
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=999, date=datetime.now(), chat=CHAT,
                       text=getattr(method, 'text', '') or '', from_user=ADMIN)

    @property
    def last_text(self) -> str:
        """Последний непустой текст: ответ на callback приходит пустым."""
        return next((text for _, text in reversed(self.calls) if text), '')


@pytest.fixture
async def admin_env(container):
    from app.admin.entities import build_entities

    await container.startup()
    container.entities = build_entities(container)

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    # как в create_bot: иначе тесты админки проверяли бы не то, что уходит
    bot.session.middleware(emoji_middleware)

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
    assert 'Админ-панель' in session.last_text


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


# ── обход всей админки ──────────────────────────────────────────────────────
#
# Кнопка «⬅️ Назад» в настройках вела в admin_main, а тот вызывал
# build_stats_text() и main_kb() вообще без аргументов — выход в /admin падал
# с TypeError. Ни один тест этого не ловил: они проверяли отдельные действия,
# но никогда не проходили меню целиком.
#
# Поэтому здесь не ещё один точечный тест, а обход: жмём каждую кнопку,
# до которой можно дойти, и требуем, чтобы ни одна не упала.

def callback_targets(markup) -> list[str]:
    return [button.callback_data
            for row in (getattr(markup, 'inline_keyboard', None) or [])
            for button in row
            if button.callback_data]


async def crawl(dp, bot, session, limit: int = 200) -> int:
    """Нажать каждую кнопку, до которой можно дойти. Вернуть, сколько нажали."""
    await dp.feed_update(bot, message('/admin'))
    queue = callback_targets(session.markups[-1])
    seen, clicked = set(queue), 0

    while queue and clicked < limit:
        data = queue.pop(0)
        clicked += 1

        session.markups.clear()
        # падение хендлера прорастёт сюда: ErrorsMiddleware в этой сборке нет
        await dp.feed_update(bot, callback(data))

        for target in (callback_targets(session.markups[-1]) if session.markups else []):
            if target not in seen:
                seen.add(target)
                queue.append(target)

    return clicked


async def test_every_button_in_the_panel_leads_somewhere(admin_env):
    dp, bot, session, container = admin_env

    clicked = await crawl(dp, bot, session)

    assert clicked > 20, f'обход прошёл всего {clicked} кнопок — меню не раскрылось'


async def test_back_from_settings_returns_to_the_panel(admin_env):
    """Ровно тот путь, который был сломан: настройки → назад → /admin."""
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='sets').pack()))
    assert 'Настройки бота' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='main').pack()))
    assert 'Админ-панель' in session.last_text


async def test_back_from_a_settings_group_returns_to_the_list(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='grp', a='pricing').pack()))
    await dp.feed_update(bot, callback(Adm(act='sets').pack()))

    assert 'Настройки бота' in session.last_text


# ── админка без кастомных эмодзи ────────────────────────────────────────────
#
# Аварийный выход. Право отправлять кастомные эмодзи есть не у каждого бота,
# и id иногда протухают — Telegram отвечает ошибкой и не доставляет сообщение
# целиком. Если бы админка ходила на тех же значках, отвалилась бы вместе со
# всем остальным, и выключить тумблер стало бы негде.

async def test_no_custom_emoji_anywhere_in_the_panel(admin_env):
    """Обход всей админки: ни одного тега и ни одной иконки на кнопке."""
    dp, bot, session, container = admin_env

    assert await container.settings.flag('content.custom_emoji') is True

    await crawl(dp, bot, session)

    with_tags = [text for _, text in session.calls if '<tg-emoji' in text]
    assert not with_tags, f'кастомные эмодзи в тексте админки: {with_tags[:3]}'

    icons = [button.text for markup in session.all_markups
             for row in markup.inline_keyboard for button in row
             if button.icon_custom_emoji_id]
    assert not icons, f'кастомные иконки на кнопках админки: {icons[:3]}'


async def test_panel_keeps_the_plain_characters(admin_env):
    """Не «убрали значки», а «оставили обычные»: подписи не должны опустеть."""
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, callback(Adm(act='sets').pack()))
    labels = [button.text for row in session.markups[-1].inline_keyboard
              for button in row]

    assert any(char in label for label in labels for char in BY_CHAR), labels


async def test_toggle_applies_without_a_restart(admin_env):
    """Тумблер, который сработает «после перезапуска», бесполезен: в аварии
    перезапускать некому и некогда."""
    from app.content import emoji

    dp, bot, session, container = admin_env
    assert emoji.enabled() is True

    await dp.feed_update(bot, callback(Adm(act='tgl', a='content.custom_emoji').pack()))
    assert emoji.enabled() is False

    await dp.feed_update(bot, callback(Adm(act='tgl', a='content.custom_emoji').pack()))
    assert emoji.enabled() is True


async def test_user_screens_still_get_custom_emoji(admin_env):
    """Обычные значки — только у админки. Проверка, что заглушили не всё."""
    dp, bot, session, _ = admin_env

    await bot.send_message(chat_id=5, text=f'{e("money")} Баланс')

    assert '<tg-emoji' in session.calls[-1][1]


async def test_broadcast_reaches_users_with_custom_emoji(admin_env):
    """Рассылка запускается из хендлера админки, и фоновая задача уносит с
    собой его контекст. Письмо при этом уходит пользователю."""
    from app.campaigns.sender import Sender

    dp, bot, session, _ = admin_env
    sent = {}

    async def handler(call, data):
        sent['ok'] = await Sender().send(bot, 5, f'{e("money")} Баланс')

    await PlainEmojiMiddleware()(handler, None, {})

    assert sent['ok'] is True
    assert '<tg-emoji' in session.calls[-1][1]
