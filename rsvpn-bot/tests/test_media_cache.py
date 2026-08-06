"""Картинки экранов: загрузка один раз, потом file_id.

Здесь проверяется то, из-за чего бот отвечал на кнопку около секунды:
каждый экран заново заливал PNG в Telegram.
"""

from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import (CallbackQuery, Chat, FSInputFile, Message, PhotoSize, User)

from app.bot.screens.base import Screen, render
from app.content.media import MediaCache, Photo, file_token

CHAT = Chat(id=5, type='private')
TG_USER = User(id=5, is_bot=False, first_name='Иван')


class FakePhotoSize:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeSent:
    """Ответ Telegram на отправку фотографии."""

    def __init__(self, file_id='AgACAgIAAx'):
        self.photo = [FakePhotoSize('маленькая'), FakePhotoSize(file_id)]


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / 'profile.png'
    path.write_bytes(b'\x89PNG' + b'0' * 2048)
    return path


def photo_of(path, cache=None) -> Photo:
    return Photo(path=str(path), token=file_token(str(path)),
                 cache=cache or MediaCache())


async def test_first_send_uploads_the_file(picture):
    photo = photo_of(picture)
    assert isinstance(photo.as_input(), FSInputFile)


async def test_second_send_reuses_the_file_id(picture):
    cache = MediaCache()
    photo = photo_of(picture, cache)

    await photo.remember(FakeSent('AgACAgIAAx'))

    # тот же файл — новый объект Photo, но идентификатор уже известен
    assert photo_of(picture, cache).as_input() == 'AgACAgIAAx'


async def test_biggest_size_is_the_one_remembered(picture):
    cache = MediaCache()
    photo = photo_of(picture, cache)

    await photo.remember(FakeSent('самая большая'))

    assert cache.get(photo.token) == 'самая большая'


async def test_replacing_the_picture_invalidates_the_cache(picture):
    """Иначе после замены файла в media/ бот показывал бы старую картинку."""
    cache = MediaCache()
    await photo_of(picture, cache).remember(FakeSent('старый-id'))

    picture.write_bytes(b'\x89PNG' + b'1' * 4096)      # другой размер

    assert isinstance(photo_of(picture, cache).as_input(), FSInputFile)


async def test_missing_photo_in_the_answer_is_not_cached(picture):
    cache = MediaCache()
    photo = photo_of(picture, cache)

    await photo.remember(object())                     # Telegram вернул не фото

    assert cache.get(photo.token) is None


async def test_cache_survives_restart(db, picture):
    """file_id хранится в базе: после перезапуска не нужно заливать заново."""
    first = MediaCache(db['media_cache'])
    await photo_of(picture, first).remember(FakeSent('AgACAgIAAx'))

    second = MediaCache(db['media_cache'])
    await second.load()

    assert photo_of(picture, second).as_input() == 'AgACAgIAAx'


async def test_broken_cache_collection_does_not_break_sending(picture):
    """Кэш — ускорение, а не обязательство: его отказ не должен ронять экран."""
    class Broken:
        def find(self, *a, **kw):
            raise RuntimeError('база недоступна')

        async def update_one(self, *a, **kw):
            raise RuntimeError('база недоступна')

    cache = MediaCache(Broken())
    await cache.load()
    photo = photo_of(picture, cache)
    await photo.remember(FakeSent('AgACAgIAAx'))

    assert cache.get(photo.token) == 'AgACAgIAAx'      # в памяти всё равно есть


# ── порядок вызовов при отрисовке ───────────────────────────────────────────
#
# Здесь настоящие объекты aiogram и настоящий Bot с подменённой сессией:
# render() различает событие по isinstance, и на заглушках эта ветка просто
# не выполнялась бы — тест проверял бы не то, что работает в проде.
class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.log: list[tuple[str, object]] = []

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.log.append((name, getattr(getattr(method, 'media', None), 'media', None)))
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=1, date=datetime.now(), chat=CHAT, from_user=TG_USER,
                       photo=[PhotoSize(file_id='AgACAgIAAx', file_unique_id='u',
                                        width=1, height=1)])


@pytest.fixture
async def bot():
    session = RecordingSession()
    telegram = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    yield telegram, session
    await telegram.session.close()


def callback(bot) -> CallbackQuery:
    message = Message(message_id=3, date=datetime.now(), chat=CHAT, text='экран',
                      from_user=TG_USER).as_(bot)
    return CallbackQuery(id='q', from_user=TG_USER, chat_instance='ci', data='d',
                         message=message).as_(bot)


async def test_spinner_stops_before_the_screen_is_sent(bot, picture):
    """«Часики» на кнопке горят до answerCallbackQuery — гасим их первыми."""
    telegram, session = bot

    await render(callback(telegram), Screen(text='экран', image=photo_of(picture)))

    assert [name for name, _ in session.log] == ['AnswerCallbackQuery', 'EditMessageMedia']


async def test_render_sends_the_file_id_on_the_second_call(bot, picture):
    telegram, session = bot
    cache = MediaCache()

    await render(callback(telegram), Screen(text='1', image=photo_of(picture, cache)))
    await render(callback(telegram), Screen(text='2', image=photo_of(picture, cache)))

    sent = [payload for name, payload in session.log if name == 'EditMessageMedia']
    assert isinstance(sent[0], FSInputFile)            # первый раз — файл
    assert sent[1] == 'AgACAgIAAx'                     # второй — идентификатор
