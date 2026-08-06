"""Пользовательские сценарии через настоящий Dispatcher aiogram."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.callbacks import Menu, Plan
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.errors import ErrorsMiddleware
from app.bot.middlewares.user import UserMiddleware

CHAT = Chat(id=5, type='private')
TG_USER = User(id=5, is_bot=False, first_name='Иван', username='ivan')


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        text = getattr(method, 'text', None) or getattr(method, 'caption', '') or ''
        self.calls.append((name, text))
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=1, date=datetime.now(), chat=CHAT, text=text,
                       from_user=TG_USER)

    @property
    def last_text(self) -> str:
        return next((text for _, text in reversed(self.calls) if text), '')


class FakeVpn:
    async def create_subscription(self, user_id, days):
        from app.core.time import now
        from datetime import timedelta
        return {'uuid': 'u-new', 'shortUuid': 's-new',
                'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    async def create_bypass_subscription(self, user_id, expire_at, traffic_bytes=None):
        return {'uuid': 'u-bypass', 'shortUuid': 's-bypass',
                'expireAt': expire_at, 'trafficLimitBytes': 1024 ** 3}

    async def update_subscription(self, uuid, **kw):
        return {}

    async def devices(self, uuid):
        return []


class FakeCryptoResponse:
    status_code = 200

    def __init__(self, url):
        self._url = url

    def json(self):
        return {'encrypted_link': f'happ://enc/{self._url.rsplit("/", 1)[-1]}'}


class FakeCryptoHttp:
    """Сервис шифрования ссылок Happ."""

    def __init__(self):
        self.calls = 0

    async def post(self, url, **kwargs):
        self.calls += 1
        return FakeCryptoResponse((kwargs.get('json') or {}).get('url', ''))


@pytest.fixture
async def env(container):
    from app.bot.handlers import register
    from app.integrations.vpn.links import LinkEncryptor
    from app.services.billing import BillingService
    from app.services.devices import DeviceBillingService
    from app.services.gifts import GiftService
    from app.services.topup import TopupService

    await container.startup()
    container.vpn = FakeVpn()
    container.topup = TopupService(container.users, container.payments_repo, container.settings)
    container.billing = BillingService(container.users, container.plans, container.settings,
                                       container.vpn, container.topup)
    container.devices = DeviceBillingService(container.users, container.settings, container.vpn)
    container.gifts = GiftService(container.users, container.db['gifts'], container.plans,
                                  container.settings, container.vpn)
    container.links = LinkEncryptor(FakeCryptoHttp())

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)

    dp = Dispatcher()
    for middleware in (ErrorsMiddleware(), DependenciesMiddleware(container),
                       UserMiddleware(container.users)):
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)
    register(dp)

    yield dp, bot, session, container
    await bot.session.close()


def message(text: str) -> Update:
    return Update(update_id=1, message=Message(
        message_id=2, date=datetime.now(), chat=CHAT, text=text, from_user=TG_USER))


def callback(data: str) -> Update:
    msg = Message(message_id=3, date=datetime.now(), chat=CHAT, text='экран', from_user=TG_USER)
    return Update(update_id=2, callback_query=CallbackQuery(
        id='q', from_user=TG_USER, chat_instance='ci', data=data, message=msg))


async def test_start_registers_user_and_shows_profile(env):
    dp, bot, session, c = env

    await dp.feed_update(bot, message('/start'))

    user = await c.users.get(5)
    assert user is not None
    assert user['info']['balance'] == 18                # стартовый баланс из настроек
    assert user['growth']['segment'] == 'new_trial_d0'
    assert 'Профиль' in session.last_text


async def test_referral_link_is_recorded(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 777}, 'info': {'ref_stats': {'referrals': []}}})

    await dp.feed_update(bot, message('/start ref_777'))

    invited = await c.users.get(5)
    referrer = await c.users.get(777)
    assert invited['user_data']['referrer'] == 777
    assert referrer['info']['ref_stats']['referrals'] == [5]


async def test_start_balance_can_be_disabled(env):
    dp, bot, session, c = env
    await c.settings.set('features.trial_enabled', False)

    await dp.feed_update(bot, message('/start'))
    assert (await c.users.get(5))['info']['balance'] == 0


async def test_subscription_screen_lists_plans_from_db(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Menu(screen='subscription').pack()))

    markup = [call for call in session.calls if call[0].startswith('Edit')]
    assert markup, 'экран тарифов не отрисовался'


async def test_buying_plan_charges_and_creates_subscription(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 200, 'тест')

    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    user = await c.users.get(5)
    assert user['vpn']['shortUuid'] == 's-new'
    assert user['info']['balance'] == 68                # 18 + 200 − 150


async def test_buying_without_money_shows_shortfall(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    assert 'не хватает 132' in session.last_text.lower()
    assert (await c.users.get(5))['vpn']['shortUuid'] == ''


async def test_disabled_feature_blocks_the_handler(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.settings.set('features.buy_enabled', False)
    await c.users.credit(5, 500, 'тест')

    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    assert (await c.users.get(5))['vpn']['shortUuid'] == ''


async def test_promo_flow_credits_balance(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.db['promo_codes'].insert_one({
        'code': 'HELLO', 'reward_type': 'balance', 'reward_value': 100,
        'max_uses': 0, 'used_count': 0, 'is_active': True, 'expires_at': None})

    await dp.feed_update(bot, callback(Menu(screen='promo').pack()))
    await dp.feed_update(bot, message('hello'))

    assert (await c.users.get(5))['info']['balance'] == 118
    assert 'активирован' in session.last_text


async def test_email_is_changed_from_the_profile_button(env):
    """Почта вводится осознанно: кнопка в профиле, потом адрес сообщением."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Menu(screen='email').pack()))
    await dp.feed_update(bot, message('не почта'))
    assert 'не похоже на адрес' in session.last_text

    await dp.feed_update(bot, message('ivan@example.com'))
    assert (await c.users.get(5))['info']['email'] == 'ivan@example.com'


async def test_plain_email_message_no_longer_overwrites_it(env):
    """Раньше любое сообщение с @ молча меняло почту."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, message('random@example.com'))

    assert (await c.users.get(5))['info']['email'] == 'Не привязана'


async def test_gift_link_activates_subscription(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))          # получатель
    await c.users.create({'user_data': {'user_id': 900}, 'info': {'balance': 500}})
    gift_id = await c.gifts.create(900, '1month')

    await dp.feed_update(bot, message(f'/start gift_{gift_id}_1month_900'))

    assert 'Подарок принят' in session.last_text
    assert (await c.users.get(5))['vpn']['shortUuid'] == 's-new'
    assert (await c.users.get(900))['info']['balance'] == 350


async def test_unknown_message_shows_profile(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    session.calls.clear()

    await dp.feed_update(bot, message('что-то непонятное'))
    assert 'Профиль' in session.last_text


async def test_subscription_screen_shows_the_price_per_period(env):
    """Раньше выводилось одно число — по нему непонятно, за что списывают."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 200, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='my_subscription').pack()))

    assert '150₽ за месяц' in session.last_text


async def test_bypass_link_is_sent_for_the_chosen_app(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 200, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))
    assert (await c.users.get(5))['vpn']['bypass_shortUuid'] == 's-bypass'

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='bypass_app', arg='happ').pack()))

    assert 'happ://enc/s-bypass' in session.last_text
    user = await c.users.get(5)
    assert user['vpn']['bypass_connectUrl'] == 'happ://enc/s-bypass'
    assert user['vpn']['preferred_client'] == 'happ'


async def test_bypass_link_is_encrypted_once_and_reused(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 200, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))

    for _ in range(3):
        await dp.feed_update(bot, callback(Menu(screen='bypass_app', arg='happ').pack()))

    assert c.links._http.calls == 1


async def test_bypass_incy_without_encoder_does_not_break_the_screen(env):
    """На Windows и в тестовом контуре node-энкодера нет — это не повод падать."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 200, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))

    await dp.feed_update(bot, callback(Menu(screen='bypass_app', arg='incy').pack()))

    assert 'bypass_connectUrl_incy' not in (await c.users.get(5))['vpn']


async def test_bypass_traffic_is_charged_by_package_price(env):
    """Цена не линейна: 15 Гб стоят 90₽, а не 15 × цену гигабайта."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))

    balance_before = (await c.users.get(5))['info']['balance']
    await dp.feed_update(bot, callback(Menu(screen='bypass_buy', arg='15').pack()))

    user = await c.users.get(5)
    assert user['info']['balance'] == balance_before - 90
    assert user['vpn']['bypass_trafficLimitBytes'] == 16 * 1024 ** 3


async def test_bypass_unknown_package_is_not_charged(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))

    balance_before = (await c.users.get(5))['info']['balance']
    await dp.feed_update(bot, callback(Menu(screen='bypass_buy', arg='7').pack()))

    assert (await c.users.get(5))['info']['balance'] == balance_before
