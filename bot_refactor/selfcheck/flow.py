import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import (CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User)

CHAT = Chat(id=802421217, type='private')
ADMIN = User(id=802421217, is_bot=False, first_name='Admin')


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self): pass

    async def stream_content(self, *a, **k):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        markup = getattr(method, 'reply_markup', None)
        self.calls.append((name, getattr(method, 'text', None), markup))
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=999, date=datetime.now(), chat=CHAT,
                       text=getattr(method, 'text', '') or '', from_user=ADMIN)


def cb(data, msg_id=100):
    message = Message(message_id=msg_id, date=datetime.now(), chat=CHAT, text='panel', from_user=ADMIN)
    return Update(update_id=1, callback_query=CallbackQuery(
        id='q1', from_user=ADMIN, chat_instance='ci', data=data, message=message))


def msg(text):
    return Update(update_id=2, message=Message(
        message_id=101, date=datetime.now(), chat=CHAT, text=text, from_user=ADMIN))


async def main():
    from core.plans import seed_plans, get_plan
    from core.settings import S
    from admin.panel import router, Adm

    await seed_plans()
    session = FakeSession()
    bot = Bot(token='123:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    dp = Dispatcher()
    dp.include_router(router)

    async def feed(update, label):
        before = len(session.calls)
        await dp.feed_update(bot, update)
        produced = session.calls[before:]
        assert produced, f'{label}: хендлер не сработал'
        text = next((c[1] for c in produced if c[1]), '')
        print(f'--- {label}\n{(text or "")[:180]}')
        return produced

    await feed(msg('/admin'), '/admin')
    await feed(cb(Adm(act='sets').pack()), 'Настройки → разделы')
    await feed(cb(Adm(act='grp', a='pricing').pack()), 'Раздел «Цены»')
    await feed(cb(Adm(act='tgl', a='features.extend_enabled').pack()), 'Тумблер «Продление»')
    assert await S.flag('features.extend_enabled') is False, 'тумблер не переключился'
    await feed(cb(Adm(act='fld', a='price.device_extra').pack()), 'Поле «Доп. устройство»')
    await feed(msg('120'), 'Ввод нового значения')
    assert await S.int('price.device_extra') == 120, 'значение не сохранилось'

    await feed(cb(Adm(act='elist', a='plan').pack()), 'Список тарифов')
    await feed(cb(Adm(act='eopen', a='plan', b='1month').pack()), 'Карточка тарифа')
    await feed(cb(Adm(act='eedit', a='plan|price', b='1month').pack()), 'Редактирование цены тарифа')
    await feed(msg('199'), 'Ввод цены')
    assert (await get_plan('1month'))['price'] == 199, 'цена тарифа не сохранилась'
    await feed(cb(Adm(act='etgl', a='plan', b='3year').pack()), 'Выключение тарифа')
    assert (await get_plan('3year'))['enabled'] is False
    await feed(cb(Adm(act='edel', a='plan', b='3year').pack()), 'Запрос удаления')
    await feed(cb(Adm(act='edelok', a='plan', b='3year').pack()), 'Удаление')
    assert await get_plan('3year') is None, 'тариф не удалён'
    await feed(cb(Adm(act='enew', a='plan').pack()), 'Создание тарифа')
    await feed(msg('1week'), 'Ввод кода тарифа')
    assert (await get_plan('1week')) is not None

    await feed(cb(Adm(act='elist', a='qr').pack()), 'Быстрые ответы')
    await feed(cb(Adm(act='main').pack()), 'Возврат в главное меню')

    print('\nFLOW OK — прогнали', len(session.calls), 'вызовов Bot API без исключений')
    await bot.session.close()

asyncio.run(main())
