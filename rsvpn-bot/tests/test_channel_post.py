"""Пост в канал от имени бота.

Главных проверок здесь две. Первая — что пост уходит именно в канал, а не
в чат админа: перепутать адресата тут стоит дороже всего. Вторая — что под
постом есть кнопка в бота с меткой: без кнопки пост уводит человека искать
бота поиском, а без метки на вопрос «что дал этот пост» отвечать нечем.
"""

import asyncio
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import panel
from app.bot.callbacks import Admin as Adm
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.emoji import emoji_middleware
from app.bot.middlewares.user import UserMiddleware
from app.services import channel as service

CHAT = Chat(id=1, type='private')
ADMIN = User(id=1, is_bot=False, first_name='Admin')


class Session(BaseSession):
    """Как RecordingSession в админских тестах, но запоминает и адресата:
    без chat_id нельзя отличить пост в канале от сообщения себе."""

    def __init__(self):
        super().__init__()
        self.sent: list[dict] = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.sent.append({
            'method': name,
            'chat_id': getattr(method, 'chat_id', None),
            'text': getattr(method, 'text', None) or getattr(method, 'caption', '') or '',
            'photo': getattr(method, 'photo', None),
            'media': getattr(method, 'media', None),
            'markup': getattr(method, 'reply_markup', None),
        })
        if name == 'AnswerCallbackQuery':
            return True
        sent = Message(message_id=777, date=datetime.now(), chat=CHAT,
                       text=getattr(method, 'text', '') or '', from_user=ADMIN)
        # sendMediaGroup отвечает списком сообщений, а не одним
        return [sent] if name == 'SendMediaGroup' else sent

    def to(self, chat_id) -> list[dict]:
        return [row for row in self.sent if row['chat_id'] == chat_id]

    def buttons(self, row: dict) -> list:
        markup = row.get('markup')
        return [button for line in (getattr(markup, 'inline_keyboard', None) or [])
                for button in line]


@pytest.fixture
async def env(container):
    from app.admin.entities import build_entities

    await container.startup()
    container.entities = build_entities(container)
    await container.settings.set('post.channel', '@rsconnect_vpn')

    session = Session()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    bot.session.middleware(emoji_middleware)

    dp = Dispatcher()
    for middleware in (DependenciesMiddleware(container),
                       UserMiddleware(container.users)):
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    dp.include_router(panel.create_router(container.config.admin_ids))

    yield dp, bot, session, container
    await bot.session.close()


def message(text: str) -> Update:
    return Update(update_id=1, message=Message(
        message_id=11, date=datetime.now(), chat=CHAT, text=text, from_user=ADMIN))


def callback(data: str) -> Update:
    msg = Message(message_id=10, date=datetime.now(), chat=CHAT, text='panel',
                  from_user=ADMIN)
    return Update(update_id=2, callback_query=CallbackQuery(
        id='q', from_user=ADMIN, chat_instance='ci', data=data, message=msg))


async def write_post(dp, bot, session, text: str = 'Большая новость') -> None:
    await dp.feed_update(bot, message('/post'))
    await dp.feed_update(bot, message(text))


# ── ссылки и метки ──────────────────────────────────────────────────────────
def test_the_channel_name_is_understood_in_any_form():
    assert service.username_of('@rsconnect_vpn') == 'rsconnect_vpn'
    assert service.username_of('https://t.me/rsconnect_vpn') == 'rsconnect_vpn'
    assert service.username_of('-1001234567890') == ''


def test_a_link_to_the_post_is_not_invented_for_a_numeric_channel():
    """Неработающая ссылка хуже, чем никакой: по ней идут и возвращаются."""
    assert service.post_url('@rsconnect_vpn', 42) == 'https://t.me/rsconnect_vpn/42'
    assert service.post_url('-1001234567890', 42) == ''


def test_the_button_carries_the_tag():
    link = service.start_link('rsconnect_bot', 'post_2209_1430')

    assert link == 'https://t.me/rsconnect_bot?start=post_2209_1430'


def test_the_tag_reads_as_a_date():
    assert service.make_tag(datetime(2026, 9, 22, 14, 30)) == 'post_2209_1430'


async def test_two_posts_in_one_minute_get_different_tags(db):
    """Иначе оба покажут одну сумму регистраций — и оба соврут."""
    moment = datetime(2026, 9, 22, 14, 30)
    first = await service.unique_tag(db, moment)
    await service.remember(db, tag=first, text='а', channel='@c', message_id=1,
                           button='б', url='u', admin_id=1)

    assert await service.unique_tag(db, moment) != first


def test_an_empty_post_is_refused():
    assert service.preview_problem('', '') != ''
    assert service.preview_problem('текст', '') == ''
    assert service.preview_problem('x' * 1100, 'file-id') != ''


# ── отправка ────────────────────────────────────────────────────────────────
async def test_the_post_goes_to_the_channel_not_to_the_admin(env):
    dp, bot, session, c = env
    await write_post(dp, bot, session, 'Сегодня скидка 30%')

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    posted = session.to('@rsconnect_vpn')
    assert len(posted) == 1
    assert 'Сегодня скидка 30%' in posted[0]['text']


async def test_the_post_has_a_button_into_the_bot(env):
    dp, bot, session, c = env
    await write_post(dp, bot, session)

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    button = session.buttons(session.to('@rsconnect_vpn')[0])[0]
    assert button.url.startswith('https://t.me/rsconnect_bot?start=post_')
    assert button.text == 'Подключить RS VPN'


async def test_the_preview_shows_the_real_post_before_it_goes(env):
    """Пост правится потом только в Telegram, поэтому увидеть его нужно
    до отправки, а не после."""
    dp, bot, session, c = env
    await write_post(dp, bot, session, 'Проверка')

    mine = [row for row in session.to(CHAT.id) if 'Проверка' in row['text']]
    assert mine and session.buttons(mine[0])[0].url.startswith('https://t.me/')
    assert not session.to('@rsconnect_vpn')      # пока ничего не ушло


async def test_the_button_label_can_be_changed_for_one_post(env):
    dp, bot, session, c = env
    await write_post(dp, bot, session)

    await dp.feed_update(bot, callback(Adm(act='postbtn').pack()))
    await dp.feed_update(bot, message('Забрать 3 дня'))
    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    assert session.buttons(session.to('@rsconnect_vpn')[0])[0].text == 'Забрать 3 дня'


async def test_the_tag_does_not_change_while_the_post_is_being_written(env):
    """Метку показывают админу в предпросмотре, и по ней он потом ищет пост
    в отчёте. Пересборка предпросмотра не должна её менять."""
    dp, bot, session, c = env
    await write_post(dp, bot, session)
    shown = _tag_from(session.to(CHAT.id))

    await dp.feed_update(bot, callback(Adm(act='postbtn').pack()))
    await dp.feed_update(bot, message('Другая надпись'))
    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    row = (await service.history(c.db))[0]
    url = session.buttons(session.to('@rsconnect_vpn')[0])[0].url
    assert shown and row['tag'] == shown and row['url'] == url


def _tag_from(rows: list[dict]) -> str:
    """Метка из первого же сообщения, где бот её назвал."""
    for row in rows:
        for word in row['text'].replace('<', ' ').replace('>', ' ').split():
            if word.startswith('post_'):
                return word
    return ''


async def test_a_refusing_telegram_does_not_pretend_the_post_went(env):
    """«Бот не админ канала» — самый частый отказ, и человек должен увидеть
    причину, а не «опубликовано»."""
    dp, bot, session, c = env
    await write_post(dp, bot, session)

    async def broken(*args, **kwargs):
        raise RuntimeError('CHAT_ADMIN_REQUIRED')

    bot.send_message = broken
    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    texts = ' '.join(row['text'] for row in session.to(CHAT.id))
    assert 'CHAT_ADMIN_REQUIRED' in texts and 'админ' in texts
    assert not await service.history(c.db)      # в журнал не записали


async def test_the_published_post_is_written_down(env):
    dp, bot, session, c = env
    await write_post(dp, bot, session, 'Текст поста')

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    row = (await service.history(c.db))[0]
    assert row['channel'] == '@rsconnect_vpn' and row['message_id'] == 777
    assert row['link'] == 'https://t.me/rsconnect_vpn/777'


# ── что дали посты ──────────────────────────────────────────────────────────
async def test_the_report_counts_who_came_from_the_post(env, user_factory):
    dp, bot, session, c = env
    await write_post(dp, bot, session)
    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))
    tag = (await service.history(c.db))[0]['tag']

    await user_factory(**{'user_data.utm': tag})
    await user_factory(**{'user_data.utm': 'ref_vlad'})

    await dp.feed_update(bot, message('/posts'))

    assert '<b>1</b> чел.' in session.to(CHAT.id)[-1]['text']


async def test_the_report_is_not_empty_without_posts(env):
    dp, bot, session, c = env

    await dp.feed_update(bot, message('/posts'))

    assert 'ещё ничего не публиковали' in session.to(CHAT.id)[-1]['text']


# ── альбомы и длина ─────────────────────────────────────────────────────────
#
# Альбом Telegram присылает несколькими сообщениями, подпись кладёт на одно
# из них и кнопку к нему прикрепить не даёт. Всё три — его правила, и
# узнавать о них в момент отправки поста поздно.

def photo_message(update_id: int, group: str = '', caption: str = '') -> Update:
    from aiogram.types import PhotoSize

    return Update(update_id=update_id, message=Message(
        message_id=20 + update_id, date=datetime.now(), chat=CHAT,
        from_user=ADMIN, media_group_id=group or None,
        caption=caption or None,
        photo=[PhotoSize(file_id=f'pic-{update_id}', file_unique_id=f'u{update_id}',
                         width=100, height=100)]))


async def send_album(dp, bot, monkeypatch, caption: str = 'Пост с картинками') -> None:
    from app.admin import channel as admin

    monkeypatch.setattr(admin, 'ALBUM_WAIT', 0.01)
    await dp.feed_update(bot, message('/post'))
    await dp.feed_update(bot, photo_message(1, 'g1', caption))
    await dp.feed_update(bot, photo_message(2, 'g1'))
    await asyncio.sleep(0.1)


async def test_an_album_becomes_one_post_not_two(env, monkeypatch):
    """Каждая картинка приходит отдельным сообщением: без сборки бот
    ответил бы двумя предпросмотрами, в каждом по одной картинке."""
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch)

    previews = [row for row in session.to(CHAT.id)
                if row['method'] in ('SendPhoto', 'SendMediaGroup')]
    assert len(previews) == 1


async def test_one_picture_is_kept_by_default_and_the_button_with_it(env,
                                                                     monkeypatch):
    """Кнопка приводит людей, альбом только показывает больше. Что взята
    первая картинка, видно в предпросмотре — молча бот её не выбирает."""
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch)

    control = session.to(CHAT.id)[-1]
    assert 'Картинок прислано 2' in control['text']
    assert any('Альбомом' in button.text for button in session.buttons(control))

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))
    posted = session.to('@rsconnect_vpn')[0]
    assert posted['method'] == 'SendPhoto'
    assert session.buttons(posted)[0].url.startswith('https://t.me/')


async def test_an_album_can_be_chosen_instead_of_the_button(env, monkeypatch):
    """Выбор настоящий: альбом Telegram не даёт снабдить кнопкой, и решать
    это должен человек."""
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch)

    await dp.feed_update(bot, callback(Adm(act='postalbum').pack()))
    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    posted = session.to('@rsconnect_vpn')
    assert len(posted) == 1 and len(posted[0]['media']) == 2
    assert not session.buttons(posted[0])


async def test_the_album_choice_says_the_button_will_be_lost(env, monkeypatch):
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch)

    await dp.feed_update(bot, callback(Adm(act='postalbum').pack()))

    assert 'без кнопки' in session.to(CHAT.id)[-1]['text']


async def test_a_long_caption_says_how_much_is_extra_and_what_to_do(env,
                                                                   monkeypatch):
    """«Длиннее 1024» без числа и без выхода — тупик, а пост уже написан."""
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch, caption='я' * 1100)

    answer = session.to(CHAT.id)[-1]['text']
    assert '1100' in answer and '76' in answer          # лишних 76 знаков
    assert 'без картинки' in answer
    assert not session.to('@rsconnect_vpn')


async def test_the_same_text_goes_through_without_a_picture(env):
    """Тот же текст, что не влез в подпись, влезает в пост без картинки."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/post'))
    await dp.feed_update(bot, message('я' * 1100))

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    posted = session.to('@rsconnect_vpn')
    assert len(posted) == 1 and session.buttons(posted[0])


async def test_a_second_post_does_not_inherit_the_first_pictures(env, monkeypatch):
    dp, bot, session, c = env
    await send_album(dp, bot, monkeypatch)
    await dp.feed_update(bot, message('/post'))
    await dp.feed_update(bot, message('Теперь просто текст'))

    await dp.feed_update(bot, callback(Adm(act='postgo').pack()))

    assert session.to('@rsconnect_vpn')[0]['method'] == 'SendMessage'
