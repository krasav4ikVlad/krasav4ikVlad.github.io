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


# ── смена токена бота ───────────────────────────────────────────────────────
#
# file_id выдаётся конкретному боту. После смены токена все запомненные
# идентификаторы стали чужими, Telegram отвечал «wrong file identifier», а
# render() гасил ошибку и уходил в текстовый запасной путь — картинки
# пропали разом на всех экранах.

def test_the_key_depends_on_the_bot(picture):
    from app.content.media import bot_scope

    old = file_token(str(picture), bot_scope('111111:AAA-old'))
    new = file_token(str(picture), bot_scope('222222:BBB-new'))

    assert old != new
    assert file_token(str(picture), bot_scope('111111:AAA-old')) == old


def test_the_key_holds_the_number_not_the_secret(picture):
    """Ключ уезжает в базу — секрету там делать нечего."""
    from app.content.media import bot_scope

    token = file_token(str(picture), bot_scope('111111:AAA-очень-секретный'))

    assert '111111' in token
    assert 'секретный' not in token


async def test_a_new_token_uploads_the_pictures_again(picture):
    from app.content.media import bot_scope

    cache = MediaCache()
    old = Photo(path=str(picture), cache=cache,
                token=file_token(str(picture), bot_scope('111111:AAA')))
    await old.remember(FakeSent('id-старого-бота'))

    new = Photo(path=str(picture), cache=cache,
                token=file_token(str(picture), bot_scope('222222:BBB')))

    assert isinstance(new.as_input(), FSInputFile)
    assert old.as_input() == 'id-старого-бота'    # чужую запись не портим


async def test_container_puts_the_bot_number_into_the_key(container, tmp_path):
    """Ключ строит контейнер — там и должен появиться номер бота."""
    import dataclasses

    (tmp_path / 'profile.png').write_bytes(b'\x89PNG' + b'0' * 16)
    container.config = dataclasses.replace(container.config, media_dir=str(tmp_path))

    first = container.media('profile')
    container.config = dataclasses.replace(container.config, bot_token='999:ZZZ')
    second = container.media('profile')

    assert first.token != second.token
    assert second.token.startswith('999:')


# ── отказ Telegram лечится перезаливкой ─────────────────────────────────────
class Rejecting:
    """Telegram, который не принимает file_id, но принимает файл."""

    def __init__(self):
        self.sent: list = []

    async def __call__(self, media):
        self.sent.append(media)
        if isinstance(media, str):
            raise RuntimeError('Bad Request: wrong file identifier')
        return FakeSent('свежий-id')


async def test_rejected_file_id_is_replaced_by_the_file(picture):
    from app.bot.screens.base import send_photo

    cache = MediaCache()
    photo = photo_of(picture, cache)
    await photo.remember(FakeSent('протухший-id'))

    telegram = Rejecting()
    await send_photo(telegram, photo)

    assert telegram.sent[0] == 'протухший-id'
    assert isinstance(telegram.sent[1], FSInputFile)     # вторая попытка — файлом
    assert cache.get(photo.token) == 'свежий-id'         # запомнили новый


async def test_the_file_is_not_sent_twice(picture):
    """Без кэша первая попытка уже идёт файлом — повторять её незачем."""
    from app.bot.screens.base import send_photo

    telegram = Rejecting()

    async def always_broken(media):
        telegram.sent.append(media)
        raise RuntimeError('канал недоступен')

    with pytest.raises(RuntimeError):
        await send_photo(always_broken, photo_of(picture))

    assert len(telegram.sent) == 1


async def test_a_second_failure_gives_up_to_the_text_fallback(picture):
    """Перезалить не вышло — ошибка идёт наверх, render() отправит текстом."""
    from app.bot.screens.base import send_photo

    cache = MediaCache()
    photo = photo_of(picture, cache)
    await photo.remember(FakeSent('протухший-id'))
    tries = []

    async def always_broken(media):
        tries.append(media)
        raise RuntimeError('и файл не принят')

    with pytest.raises(RuntimeError):
        await send_photo(always_broken, photo)

    assert len(tries) == 2
    assert cache.get(photo.token) is None        # протухшее из кэша убрали


async def test_forgetting_also_clears_the_database(db, picture):
    """Иначе перезапуск поднял бы протухший file_id обратно из базы."""
    cache = MediaCache(db['media_cache'])
    photo = photo_of(picture, cache)
    await photo.remember(FakeSent('протухший-id'))

    await photo.forget()

    restarted = MediaCache(db['media_cache'])
    await restarted.load()
    assert restarted.get(photo.token) is None
