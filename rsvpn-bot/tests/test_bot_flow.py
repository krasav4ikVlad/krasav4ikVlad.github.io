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
        self.markups: list = []
        self.results: list = []

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        text = getattr(method, 'text', None) or getattr(method, 'caption', '') or ''
        self.calls.append((name, text))
        self.markups.append(getattr(method, 'reply_markup', None))
        self.results.extend(getattr(method, 'results', None) or [])
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=1, date=datetime.now(), chat=CHAT, text=text,
                       from_user=TG_USER)

    @property
    def last_text(self) -> str:
        return next((text for _, text in reversed(self.calls) if text), '')

    def buttons(self) -> list:
        """Все кнопки последней отправленной клавиатуры."""
        markup = next((m for m in reversed(self.markups) if m is not None), None)
        return [b for row in (getattr(markup, 'inline_keyboard', None) or []) for b in row]


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
    from app.admin import panel as admin_panel
    from app.bot.handlers import register
    from app.integrations.payments.heleket import HeleketProvider
    from app.integrations.payments.registry import PaymentRegistry
    from app.integrations.vpn.links import LinkEncryptor
    from app.services.notifier import Notifier
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
    container.payments = PaymentRegistry(
        [HeleketProvider('key', 'merchant', FakeCryptoHttp())], container.settings)

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    container.notifier = Notifier(bot, container.settings, container.users)

    dp = Dispatcher()
    for middleware in (ErrorsMiddleware(), DependenciesMiddleware(container),
                       UserMiddleware(container.users)):
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)
    # как в factory.create_dispatcher: инлайн-режиму нужны свои middleware,
    # иначе хендлер подарков падает на отсутствии зависимостей
    dp.inline_query.outer_middleware(ErrorsMiddleware())
    dp.inline_query.outer_middleware(DependenciesMiddleware(container))
    # админом делаем самого тестового пользователя: так проверяются и кнопки
    # под заявкой на вывод, и фильтр «только админ» на реальном роутере
    dp.include_router(admin_panel.create_router((TG_USER.id,)))
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


# ── вывод реферального баланса ──────────────────────────────────────────────
async def add_sbp_method(dp, bot, uid=5):
    """Проходит экраны добавления реквизитов так же, как это делает человек."""
    from app.bot.callbacks import Payout

    await dp.feed_update(bot, callback(Payout(action='add').pack()))
    await dp.feed_update(bot, callback(Payout(action='type', value='sbp').pack()))
    for field, value in (('fio', 'Иванов Иван Иванович'),
                         ('phone', '+79001234567'),
                         ('bank', 'Сбербанк')):
        await dp.feed_update(bot, callback(Payout(action='field', value=field).pack()))
        await dp.feed_update(bot, message(value))
    await dp.feed_update(bot, callback(Payout(action='save').pack()))


async def test_payout_method_is_added_field_by_field(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await add_sbp_method(dp, bot)

    methods = await c.payouts.methods(5)
    assert len(methods) == 1
    assert methods[0]['data'] == {'fio': 'Иванов Иван Иванович',
                                  'phone': '+79001234567', 'bank': 'Сбербанк'}


async def test_payout_screen_hides_the_full_card_number(env):
    """Экран может попасть на скриншот — полный номер там не нужен."""
    from app.bot.callbacks import Payout

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Payout(action='add').pack()))
    await dp.feed_update(bot, callback(Payout(action='type', value='mir').pack()))
    for field, value in (('fio', 'Иванов Иван'), ('card', '2200123412341234')):
        await dp.feed_update(bot, callback(Payout(action='field', value=field).pack()))
        await dp.feed_update(bot, message(value))
    await dp.feed_update(bot, callback(Payout(action='save').pack()))

    method_id = (await c.payouts.methods(5))[0]['id']
    session.calls.clear()
    await dp.feed_update(bot, callback(Payout(action='view', value=method_id).pack()))

    assert '220012******1234' in session.last_text
    assert '2200123412341234' not in session.last_text


async def test_payout_request_reaches_admins_with_the_requisites(env):
    """Раньше заявка не уходила никуда: notifier в контейнере был None."""
    from app.bot.callbacks import Payout

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'info.ref_stats.withdrawable': 900}})
    await add_sbp_method(dp, bot)

    session.calls.clear()
    await dp.feed_update(bot, callback(Payout(action='order').pack()))

    card = next((text for _, text in session.calls if 'Заявка на вывод' in text), '')
    assert card, 'заявка не ушла в админ-чат'
    assert '900₽' in card
    assert '+79001234567' in card and 'Сбербанк' in card
    assert (await c.users.get(5))['info']['ref_stats']['pending_payout_active'] is True


async def test_payout_request_below_minimum_is_not_claimed(env):
    from app.bot.callbacks import Payout

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'info.ref_stats.withdrawable': 100}})

    await dp.feed_update(bot, callback(Payout(action='order').pack()))

    stats = (await c.users.get(5))['info']['ref_stats']
    assert not stats.get('pending_payout_active')


async def test_payout_to_balance_moves_the_money_and_closes_the_request(env):
    """Кнопка админа под заявкой: сервис это умел, но вызвать было неоткуда."""
    from app.bot.callbacks import Payout, PayoutAdmin

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'info.ref_stats.withdrawable': 900}})
    await dp.feed_update(bot, callback(Payout(action='order').pack()))

    await dp.feed_update(bot, callback(
        PayoutAdmin(action='balance', user_id=5).pack()))

    user = await c.users.get(5)
    assert user['info']['ref_stats']['withdrawable'] == 0
    assert user['info']['balance'] == 918                 # 18 стартовых + 900
    assert user['info']['ref_stats']['pending_payout_active'] is False


async def test_payout_rejection_keeps_the_money(env):
    from app.bot.callbacks import Payout, PayoutAdmin

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'info.ref_stats.withdrawable': 900}})
    await dp.feed_update(bot, callback(Payout(action='order').pack()))

    await dp.feed_update(bot, callback(
        PayoutAdmin(action='reject', user_id=5, reason='data').pack()))

    stats = (await c.users.get(5))['info']['ref_stats']
    assert stats['withdrawable'] == 900                   # отказ — не изъятие
    assert stats['pending_payout_active'] is False


# ── экран пополнения ────────────────────────────────────────────────────────
async def test_topup_screen_shows_the_bonus_that_is_actually_credited(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'growth.ab_group': 'bonus_30'}})

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    assert '+30% сверху' in session.last_text
    assert 'Плата за подписку' in session.last_text


async def test_topup_screen_promises_nothing_without_the_bonus(env):
    """Обещать бонус тому, кому его не начислят, — хуже, чем не обещать."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'growth.ab_group': 'control'}})

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    assert 'сверху' not in session.last_text


async def test_topup_bonus_is_not_promised_after_it_was_used(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'growth.ab_group': 'used'}})

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    assert 'сверху' not in session.last_text


async def test_close_button_deletes_the_message(env):
    """Сообщения со ссылками копятся в чате — их должно быть чем убрать."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    session.calls.clear()

    await dp.feed_update(bot, callback(Menu(screen='close').pack()))

    assert any(name == 'DeleteMessage' for name, _ in session.calls)


# ── рассылка из админки ─────────────────────────────────────────────────────
async def test_broadcast_asks_audience_then_text_then_confirms(env):
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Adm(act='broadcast').pack()))
    assert 'Кому отправляем' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='bcseg', a='all').pack()))
    assert 'Отправьте текст' in session.last_text

    await dp.feed_update(bot, message('Привет, это рассылка'))
    assert 'Так увидят получатели' in session.last_text


async def test_broadcast_refuses_an_empty_message(env):
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await dp.feed_update(bot, callback(Adm(act='broadcast').pack()))
    await dp.feed_update(bot, callback(Adm(act='bcseg', a='all').pack()))

    await dp.feed_update(bot, message('   '))
    assert 'Пустое сообщение' in session.last_text


async def test_broadcast_reaches_only_the_chosen_segment(env):
    from app.admin.broadcast import _query, _run

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    for user_id, segment in ((11, 'expired_3d'), (12, 'expired_7d'), (13, 'active_paid')):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': segment}})

    session.calls.clear()
    msg = Message(message_id=9, date=datetime.now(), chat=CHAT, text='отчёт',
                  from_user=TG_USER).as_(bot)
    await _run(c, bot, msg, _query('expired'), 'Возвращайтесь!')

    delivered = [text for name, text in session.calls if text == 'Возвращайтесь!']
    assert len(delivered) == 2                       # 11 и 12, но не 13
    assert 'Доставлено: <code>2</code>' in session.last_text


# ── смена длительности ──────────────────────────────────────────────────────
async def test_changing_period_does_not_charge_anything(env):
    """Меняется только период: списание произойдёт при следующем продлении."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1day').pack()))

    balance_before = (await c.users.get(5))['info']['balance']
    expire_before = (await c.users.get(5))['vpn']['expireAt']

    await dp.feed_update(bot, callback(Plan(action='change', code='1month').pack()))

    user = await c.users.get(5)
    assert user['vpn']['period'] == 30
    assert user['info']['balance'] == balance_before      # деньги не тронуты
    assert user['vpn']['expireAt'] == expire_before       # дата тоже


async def test_period_can_be_changed_without_money_on_the_balance(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1day').pack()))
    await c.users.col.update_one({'user_data.user_id': 5}, {'$set': {'info.balance': 0}})

    await dp.feed_update(bot, callback(Plan(action='change', code='3month').pack()))

    assert (await c.users.get(5))['vpn']['period'] == 90


async def test_unbind_survives_leaving_the_screen(env):
    """Раньше hwid лежал в состоянии диалога и терялся при переходе в профиль."""
    from app.bot.callbacks import Devices
    from app.bot.handlers.devices import device_token

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    unbound = []

    async def devices(uuid):
        return [d for d in [{'hwid': 'HW-очень-длинный-идентификатор-устройства',
                             'deviceModel': 'iPhone'}]
                if d['hwid'] not in unbound]

    async def delete_device(uuid, hwid):
        unbound.append(hwid)
        return True

    c.vpn.devices = devices
    c.vpn.delete_device = delete_device

    await dp.feed_update(bot, callback(Devices(action='list').pack()))
    await dp.feed_update(bot, callback(Menu(screen='profile').pack()))   # ушёл и вернулся

    token = device_token('HW-очень-длинный-идентификатор-устройства')
    await dp.feed_update(bot, callback(Devices(action='unbind', value=token).pack()))

    assert unbound == ['HW-очень-длинный-идентификатор-устройства']


def test_device_token_fits_into_a_callback():
    """У Telegram на всё поле 64 байта — именно на этом ломалась отвязка."""
    from app.bot.callbacks import Devices
    from app.bot.handlers.devices import device_token

    packed = Devices(action='unbind', value=device_token('x' * 300)).pack()
    assert len(packed.encode()) <= 64


# ── платёжные методы ────────────────────────────────────────────────────────
async def test_tribute_button_leads_straight_to_the_mini_app(env):
    """У Tribute сумма выбирается в его интерфейсе — наш экран суммы лишний."""
    from app.integrations.payments.registry import PaymentRegistry
    from app.integrations.payments.tribute import TributeProvider

    dp, bot, session, c = env
    c.payments = PaymentRegistry(
        [TributeProvider('key', title='💳 Карта РФ'),
         TributeProvider('key', code='tribute_eu', title='🌐 Карта иностранная')],
        c.settings)
    await dp.feed_update(bot, message('/start'))

    session.markups.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    tribute = [b for b in session.buttons() if 'Карта' in b.text]
    assert len(tribute) == 2, 'обе карточные кнопки должны быть на экране'
    assert all(b.url == 'https://t.me/tribute/app?startapp=dNvx' for b in tribute)
    assert all(b.callback_data is None for b in tribute)


async def test_other_providers_still_ask_for_the_amount(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    session.markups.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    crypto = next(b for b in session.buttons() if 'Криптовалюта' in b.text)
    assert crypto.url is None and crypto.callback_data


# ── подарки в инлайн-режиме ─────────────────────────────────────────────────
def inline_query(text: str = '') -> Update:
    from aiogram.types import InlineQuery
    return Update(update_id=8, inline_query=InlineQuery(
        id='iq', from_user=TG_USER, query=text, offset=''))


async def test_inline_gifts_offer_all_plans_without_a_query(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    session.results.clear()
    await dp.feed_update(bot, inline_query(''))

    assert len(session.results) == len(await c.plans.all())
    assert all('Подарить' in r.title for r in session.results)


async def test_inline_gifts_filter_by_the_chosen_plan(env):
    """Кнопка «Подарить» подставляет код тарифа через switch_inline_query."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    session.results.clear()
    await dp.feed_update(bot, inline_query('1month'))

    assert len(session.results) == 1


async def test_inline_gifts_reuse_the_pending_gift(env):
    """Telegram шлёт запрос на каждое нажатие клавиши — база не должна пухнуть."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    for _ in range(5):
        await dp.feed_update(bot, inline_query('1month'))

    created = [g for g in c.db['gifts'].docs if g['plan_code'] == '1month']
    assert len(created) == 1
