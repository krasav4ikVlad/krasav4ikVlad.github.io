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


class FakeMember:
    def __init__(self, status='member', is_member=True):
        self.status = status
        self.is_member = is_member


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
    from app.services.billing import BillingService
    from app.services.devices import DeviceBillingService
    from app.services.gifts import GiftService
    from app.services.topup import TopupService

    import dataclasses
    # В проде и роутер, и проверки внутри хендлеров берут один и тот же
    # config.admin_ids. В тестах должно быть так же, иначе «админа нельзя
    # забанить» проверялось бы против другого списка.
    container.config = dataclasses.replace(container.config, admin_ids=(TG_USER.id,))

    await container.startup()
    container.vpn = FakeVpn()
    container.topup = TopupService(container.users, container.payments_repo, container.settings)
    container.billing = BillingService(container.users, container.plans, container.settings,
                                       container.vpn, container.topup,
                                       discounts=container.discounts)
    container.devices = DeviceBillingService(container.users, container.settings, container.vpn)
    container.gifts = GiftService(container.users, container.db['gifts'], container.plans,
                                  container.settings, container.vpn,
                                  discounts=container.discounts)
    container.links = LinkEncryptor(FakeCryptoHttp())
    container.trial.vpn = container.vpn
    container.moderation.vpn = container.vpn
    container.payments = PaymentRegistry(
        [HeleketProvider('key', 'merchant', FakeCryptoHttp())], container.settings)

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    # как в main_bot: без attach_bot остаются пустыми devices, renewal,
    # expiry, lifeline и notifier — тесты должны идти по той же сборке
    container.attach_bot(bot)
    assert container.missing_services() == []

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
    dp.include_router(admin_panel.create_router(container.config.admin_ids))
    register(dp)
    # BanMiddleware в проде висит на диспетчере — здесь тоже, иначе бан
    # проверялся бы не так, как работает
    from app.bot.middlewares.ban import BanMiddleware
    ban = BanMiddleware(container.settings, ())
    dp.message.outer_middleware(ban)
    dp.callback_query.outer_middleware(ban)

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
    # денег за регистрацию больше не дают: вместо них бесплатный период
    assert user['info']['balance'] == 0
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


async def test_start_balance_can_be_turned_back_on(env):
    """Раздача денег никуда не делась — она просто выключена по умолчанию."""
    dp, bot, session, c = env
    await c.settings.set('price.start_balance', 50)

    await dp.feed_update(bot, message('/start'))
    assert (await c.users.get(5))['info']['balance'] == 50


async def test_free_period_toggle_does_not_hand_out_money(env):
    """Тумблер бесплатного периода к балансу отношения не имеет."""
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
    assert user['info']['balance'] == 50                # 200 − 150


async def test_buying_without_money_shows_shortfall(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    assert 'не хватает 150' in session.last_text.lower()
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

    assert (await c.users.get(5))['info']['balance'] == 100
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
    assert user['info']['balance'] == 900
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
    """Обещать бонус тому, кому его не начислят, — хуже, чем не обещать.

    Надбавка новичка живёт ровно на триальных сегментах: вне их экран молчит,
    какой бы ни была A/B-группа.
    """
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.col.update_one({'user_data.user_id': 5},
                                 {'$set': {'growth.ab_group': 'bonus_30',
                                           'growth.segment': 'expired_7d'}})

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
    from app.domain.segments import audience_query

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    for user_id, segment in ((11, 'expired_3d'), (12, 'expired_7d'), (13, 'active_paid')):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': segment}})

    session.calls.clear()
    await run_broadcast(c, bot, audience_query('expired'), 'Возвращайтесь!')

    delivered = [text for name, text in session.calls if text == 'Возвращайтесь!']
    assert len(delivered) == 2                       # 11 и 12, но не 13
    assert '<b>Доставлено:</b> <code>2</code>' in session.last_text


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


async def test_inline_gifts_explain_themselves_instead_of_staying_silent(env):
    """Пустой ответ Telegram рисует как «ничего не найдено» — не отличить от поломки."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.settings.set('features.gifts_enabled', False)

    session.calls.clear()
    await dp.feed_update(bot, inline_query(''))

    assert any(name == 'AnswerInlineQuery' for name, _ in session.calls)


async def test_inline_gifts_answer_unknown_query_with_a_button(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    session.results.clear()
    await dp.feed_update(bot, inline_query('такого тарифа нет'))

    assert session.results == []


# ── продление ───────────────────────────────────────────────────────────────
async def test_extend_patches_the_subscription_instead_of_creating_a_new_one(env):
    """Раньше продление вызывало buy() → POST /api/users, и панель отвечала
    «User short UUID already exists»: shortUuid считается от user_id."""
    dp, bot, session, c = env
    created, patched = [], []

    async def create_subscription(user_id, days):
        from datetime import timedelta
        from app.core.time import now
        created.append(user_id)
        return {'uuid': 'u-new', 'shortUuid': 's-new',
                'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    async def update_subscription(uuid, **kw):
        patched.append((uuid, kw))
        return {}

    c.vpn.create_subscription = create_subscription
    c.vpn.update_subscription = update_subscription

    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    created.clear()

    await dp.feed_update(bot, callback(Menu(screen='extend').pack()))

    assert created == [], 'продление не должно создавать новую подписку'
    assert patched and patched[0][0] == 'u-new'
    assert 'expire_at' in patched[0][1]


async def test_extend_moves_both_the_subscription_and_bypass(env):
    dp, bot, session, c = env
    patched = []

    async def update_subscription(uuid, **kw):
        patched.append(uuid)
        return {}

    c.vpn.update_subscription = update_subscription

    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await dp.feed_update(bot, callback(Menu(screen='bypass_create').pack()))
    patched.clear()

    await dp.feed_update(bot, callback(Menu(screen='extend').pack()))

    assert patched == ['u-new', 'u-bypass']
    user = await c.users.get(5)
    assert user['vpn']['expireAt'] == user['vpn']['bypass_expireAt']


async def test_extend_adds_the_period_to_the_current_date(env):
    dp, bot, session, c = env
    from datetime import timedelta
    c.vpn.update_subscription = lambda uuid, **kw: __import__('asyncio').sleep(0, result={})

    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    before = (await c.users.get(5))['vpn']['expireAt']

    await dp.feed_update(bot, callback(Menu(screen='extend').pack()))

    after = (await c.users.get(5))['vpn']['expireAt']
    assert after - before == timedelta(days=30)


async def test_extend_without_money_does_not_touch_the_subscription(env):
    dp, bot, session, c = env
    patched = []
    c.vpn.update_subscription = lambda uuid, **kw: patched.append(uuid) or \
        __import__('asyncio').sleep(0, result={})

    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await c.users.col.update_one({'user_data.user_id': 5}, {'$set': {'info.balance': 10}})
    patched.clear()

    await dp.feed_update(bot, callback(Menu(screen='extend').pack()))

    assert patched == []
    assert (await c.users.get(5))['info']['balance'] == 10


# ── отвязка всех устройств ──────────────────────────────────────────────────
async def test_unbind_all_asks_before_doing_it(env):
    from app.bot.callbacks import Devices

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    removed = []
    c.vpn.devices = lambda uuid: __import__('asyncio').sleep(
        0, result=[{'hwid': 'a'}, {'hwid': 'b'}])
    c.vpn.delete_device = lambda uuid, hwid: removed.append(hwid) or \
        __import__('asyncio').sleep(0, result=True)

    await dp.feed_update(bot, callback(Devices(action='unbind_all').pack()))

    assert 'Отвязать все' in session.last_text
    assert removed == [], 'до подтверждения ничего отвязывать нельзя'

    await dp.feed_update(bot, callback(Devices(action='unbind_all_ok').pack()))
    assert removed == ['a', 'b']


async def test_extend_without_money_offers_a_top_up(env):
    """Всплывающий текст — тупик: из него некуда идти пополнять."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))
    await c.users.col.update_one({'user_data.user_id': 5}, {'$set': {'info.balance': 10}})

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='extend').pack()))

    assert 'не хватает 140' in session.last_text.lower()


# ── блокировка пользователей ────────────────────────────────────────────────
async def test_banned_user_gets_no_screens(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.moderation.ban(5, admin_id=1, reason='спам')
    # middleware в этой сборке не знает про админов — проверяем сам механизм

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='payments').pack()))

    assert not [name for name, _ in session.calls if name.startswith('Edit')]


async def test_banned_user_is_told_why(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.moderation.ban(5, admin_id=1)

    session.calls.clear()
    await dp.feed_update(bot, message('/start'))

    assert 'ограничен' in session.last_text


async def test_silent_mode_says_nothing_at_all(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.moderation.ban(5, admin_id=1)
    await c.settings.set('moderation.ban_silent', True)

    session.calls.clear()
    await dp.feed_update(bot, message('/start'))

    assert session.calls == []


async def test_unban_restores_access(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.moderation.ban(5, admin_id=1)
    await c.moderation.unban(5, admin_id=1)

    session.calls.clear()
    await dp.feed_update(bot, message('/start'))

    assert 'Профиль' in session.last_text


async def test_ban_does_not_touch_the_subscription_or_money(env):
    """Бан — запрет на общение с ботом, а не изъятие оплаченного."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    await c.users.credit(5, 500, 'тест')
    await dp.feed_update(bot, callback(Plan(action='buy', code='1month').pack()))

    await c.moderation.ban(5, admin_id=1)

    user = await c.users.get(5)
    assert user['vpn']['shortUuid'] == 's-new'
    assert user['info']['balance'] == 350


async def test_ban_command_finds_user_by_username(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 900, 'username': 'petya'},
                          'info': {'balance': 0}})

    await dp.feed_update(bot, message('/ban @petya надоел'))

    user = await c.users.get(900)
    assert user['moderation']['banned'] is True
    assert user['moderation']['reason'] == 'надоел'


async def test_ban_command_finds_user_by_id(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 900}, 'info': {'balance': 0}})

    await dp.feed_update(bot, message('/ban 900'))
    assert (await c.users.get(900))['moderation']['banned'] is True

    await dp.feed_update(bot, message('/unban 900'))
    assert (await c.users.get(900))['moderation']['banned'] is False


async def test_ban_command_reports_an_unknown_user(env):
    dp, bot, session, c = env

    await dp.feed_update(bot, message('/ban 404404'))
    assert 'не найден' in session.last_text


async def test_admin_cannot_be_banned(env):
    """Иначе одной опечаткой можно отрезать себя от собственной админки."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    await dp.feed_update(bot, message('/ban 5'))

    assert 'Администратора' in session.last_text
    assert not (await c.users.get(5)).get('moderation', {}).get('banned')


# ── бесплатный период за подписку ───────────────────────────────────────────
async def test_free_period_is_offered_to_a_newcomer(env):
    dp, bot, session, c = env
    session.markups.clear()

    await dp.feed_update(bot, message('/start'))

    assert any('бесплатно' in b.text for b in session.buttons())


async def test_free_period_needs_a_channel_subscription(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    async def not_subscribed(chat_id, user_id):
        return FakeMember(status='left', is_member=False)

    bot.get_chat_member = not_subscribed
    c.trial.bot = bot

    await dp.feed_update(bot, callback(Menu(screen='trial_claim').pack()))

    assert (await c.users.get(5))['vpn']['shortUuid'] == ''


async def test_subscriber_gets_the_free_period(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    async def subscribed(chat_id, user_id):
        return FakeMember(status='member')

    bot.get_chat_member = subscribed
    c.trial.bot = bot

    await dp.feed_update(bot, callback(Menu(screen='trial_claim').pack()))

    user = await c.users.get(5)
    assert user['vpn']['shortUuid'] == 's-new'
    assert user['vpn']['period'] == 3
    assert user['growth']['trial_claimed_at']
    assert user['info']['balance'] == 0        # деньгами по-прежнему не сыпем


async def test_free_period_is_given_only_once(env):
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    created = []
    original = c.vpn.create_subscription

    async def counting(user_id, days):
        created.append(days)
        return await original(user_id, days)

    c.vpn.create_subscription = counting
    bot.get_chat_member = lambda chat_id, user_id: __import__('asyncio').sleep(
        0, result=FakeMember())
    c.trial.bot = bot

    await dp.feed_update(bot, callback(Menu(screen='trial_claim').pack()))
    await c.users.set_vpn(5, {'shortUuid': '', 'uuid': ''})   # как будто подписка ушла
    await dp.feed_update(bot, callback(Menu(screen='trial_claim').pack()))

    assert created == [3], 'бесплатный период не должен выдаваться дважды'


async def test_unverifiable_subscription_is_not_the_users_fault(env):
    """Бота не добавили админом канала — виноваты мы, а отказ получает человек."""
    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))

    async def broken(chat_id, user_id):
        raise RuntimeError('bot is not a member of the channel')

    bot.get_chat_member = broken
    c.trial.bot = bot

    session.calls.clear()
    await dp.feed_update(bot, callback(Menu(screen='trial_claim').pack()))

    assert (await c.users.get(5))['vpn']['shortUuid'] == ''
    assert any('поддержку' in text.lower() for _, text in session.calls)


async def test_hardban_command_disables_the_subscription(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 900, 'username': 'petya'},
                          'info': {'balance': 0},
                          'vpn': {'uuid': 'u-900', 'bypass_uuid': 'b-900'}})

    statuses = []
    c.vpn.set_status = lambda uuid, status: statuses.append((uuid, status)) or \
        __import__('asyncio').sleep(0, result={})

    await dp.feed_update(bot, message('/hardban 900 чарджбек'))

    assert statuses == [('u-900', 'DISABLED'), ('b-900', 'DISABLED')]
    saved = (await c.users.get(900))['moderation']
    assert saved['banned'] is True and saved['hard'] is True
    assert 'Жёстко заблокирован' in session.last_text


async def test_unban_after_hardban_restores_the_subscription(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 900}, 'info': {'balance': 0},
                          'vpn': {'uuid': 'u-900'}})

    statuses = []
    c.vpn.set_status = lambda uuid, status: statuses.append((uuid, status)) or \
        __import__('asyncio').sleep(0, result={})

    await dp.feed_update(bot, message('/hardban 900'))
    await dp.feed_update(bot, message('/unban 900'))

    assert statuses == [('u-900', 'DISABLED'), ('u-900', 'ACTIVE')]
    assert (await c.users.get(900))['moderation']['hard'] is False


async def test_plain_ban_does_not_call_the_panel(env):
    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 900}, 'info': {'balance': 0},
                          'vpn': {'uuid': 'u-900'}})

    statuses = []
    c.vpn.set_status = lambda uuid, status: statuses.append((uuid, status)) or \
        __import__('asyncio').sleep(0, result={})

    await dp.feed_update(bot, message('/ban 900'))

    assert statuses == []
    assert '/hardban 900' in session.last_text      # подсказка, как ужесточить


def last_markup(session):
    """Последняя непустая клавиатура: ответ на callback уходит без неё."""
    return next(m for m in reversed(session.markups) if m is not None)


async def test_device_buttons_go_one_per_row(env):
    """Две кнопки в ряд обрезаются на телефоне: «Увеличить на 3 за 225₽»
    превращается в «Увеличить на…», и не видно ни числа, ни цены."""
    from app.bot.callbacks import Devices
    from app.bot.handlers.devices import PACKAGES
    from app.content.emoji import e

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    session.markups.clear()
    await dp.feed_update(bot, callback(Menu(screen='devices').pack()))

    rows = last_markup(session).inline_keyboard
    add_rows = [row for row in rows
                if any('Увеличить' in button.text for button in row)]

    assert len(add_rows) == len(PACKAGES)
    assert all(len(row) == 1 for row in add_rows), 'кнопки снова встали парами'
    for amount, row in zip(PACKAGES, add_rows):
        assert row[0].text.startswith(e(f'plus{amount}')), row[0].text
    assert Devices(action='add', value='1').pack() == add_rows[0][0].callback_data


async def test_unbind_all_is_marked_with_a_minus(env):
    from app.bot.callbacks import Devices
    from app.content.emoji import e

    dp, bot, session, c = env
    await dp.feed_update(bot, message('/start'))
    c.vpn.devices = lambda uuid: __import__('asyncio').sleep(
        0, result=[{'hwid': 'HW-1', 'deviceModel': 'iPhone'}])
    await c.users.set_vpn(5, {'uuid': 'u', 'shortUuid': 's'})

    session.markups.clear()
    await dp.feed_update(bot, callback(Devices(action='list').pack()))

    labels = [button.text for row in last_markup(session).inline_keyboard
              for button in row]
    assert any(label.startswith(f'{e("minus")} Отвязать все') for label in labels), labels


# ── счётчик рассылки по ходу дела ───────────────────────────────────────────
#
# Раньше админ видел «рассылка запущена» и потом молчание на несколько минут:
# понять, идёт она вообще или зависла, было нельзя.

async def run_broadcast(c, bot, query: dict, text: str, job_id: str = 'job-1'):
    """Завести задание и прогнать его — как это делает start_sending."""
    from app.admin.broadcast import _recipients, _run

    await c.db['broadcasts'].insert_one({
        '_id': job_id, 'text': text, 'recipients': await _recipients(c, query),
        'position': 0, 'sent': 0, 'failed': 0, 'status': 'running',
        'chat_id': CHAT.id, 'message_id': 9})
    await _run(c, bot, job_id)
    return await c.db['broadcasts'].find_one({'_id': job_id})


async def broadcast_env(env, users: int, step: int, fail_every: int = 0):
    """База из N человек и рассылка по ним. Возвращает правки сообщения."""
    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', step)
    for user_id in range(100, 100 + users):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    if fail_every:
        original = bot.session.make_request
        counter = {'n': 0}

        async def flaky(bot_, method, timeout=None):
            # именно TelegramForbiddenError: так выглядит заблокировавший
            # бота. Обычное исключение Sender считает сбоем сети и повторяет
            # три раза — тогда «не доставлено» осталось бы нулём.
            from aiogram.exceptions import TelegramForbiddenError

            if type(method).__name__ == 'SendMessage':
                counter['n'] += 1
                if counter['n'] % fail_every == 0:
                    raise TelegramForbiddenError(method=method,
                                                 message='bot was blocked by the user')
            return await original(bot_, method, timeout)

        bot.session.make_request = flaky

    session.calls.clear()
    await run_broadcast(c, bot, {'growth.segment': 'expired_3d'}, 'Привет!')

    return [text for name, text in session.calls if name == 'EditMessageText']


async def test_counters_update_every_hundred(env):
    edits = await broadcast_env(env, users=250, step=100)

    # 100, 200 и итог — три правки, не двести пятьдесят
    assert len(edits) == 3, edits
    assert '<code>100</code>' in edits[0]
    assert '<code>200</code>' in edits[1]
    assert 'завершена' in edits[-1]


async def test_progress_shows_what_is_left(env):
    edits = await broadcast_env(env, users=150, step=100)

    assert 'Осталось:</b> <code>50</code> из <code>150</code>' in edits[0]


async def test_undelivered_are_counted_separately(env):
    """Заблокировавшие бота — это не ошибка рассылки, но их надо видеть."""
    edits = await broadcast_env(env, users=100, step=50, fail_every=10)

    assert '<b>Не доставлено:</b> <code>5</code>' in edits[0]
    assert '<b>Доставлено:</b> <code>90</code>' in edits[-1]
    assert '<b>Не доставлено:</b> <code>10</code>' in edits[-1]


async def test_short_broadcast_still_reports_at_the_end(env):
    """Получателей меньше шага — промежуточных правок нет, итог есть."""
    edits = await broadcast_env(env, users=5, step=100)

    assert len(edits) == 1
    assert 'завершена' in edits[0]
    assert '<b>Доставлено:</b> <code>5</code>' in edits[0]


async def test_zero_step_turns_the_counter_off(env):
    edits = await broadcast_env(env, users=250, step=0)

    assert len(edits) == 1, 'при шаге 0 промежуточных правок быть не должно'


async def test_a_broken_edit_does_not_stop_the_broadcast(env):
    """Правка сообщения упирается в лимит Telegram чаще, чем отправка.
    Рассылка идёт ради писем, а не ради отчёта."""
    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', 10)
    for user_id in range(200, 230):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    original = bot.session.make_request

    async def no_edits(bot_, method, timeout=None):
        if type(method).__name__ == 'EditMessageText':
            raise RuntimeError('Too Many Requests: retry after 5')
        return await original(bot_, method, timeout)

    bot.session.make_request = no_edits
    session.calls.clear()

    await run_broadcast(c, bot, {'growth.segment': 'expired_3d'}, 'Привет!')

    delivered = [t for name, t in session.calls if t == 'Привет!']
    assert len(delivered) == 30, 'письма должны уйти все'


# ── рассылка, которая оборвалась ────────────────────────────────────────────
#
# «Остановилась и не завершилась» — это два разных случая, и оба раньше
# выглядели одинаково: счётчик замер, лог пуст, отчёта нет.

async def test_a_crash_is_visible_and_resumable(env):
    """Падение посреди отправки: раньше исключение оседало внутри Task и
    никуда не попадало — счётчик просто замирал.

    Ломаем запись состояния, а не отправку: ошибки отправки Sender гасит
    сам (заблокировавший бота — норма), а вот отказ базы прорастает наружу.
    """
    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', 5)
    for user_id in range(300, 320):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    from app.admin.broadcast import _recipients, _run

    recipients = await _recipients(c, {'growth.segment': 'expired_3d'})
    await c.db['broadcasts'].insert_one({
        '_id': 'job-1', 'text': 'Привет!', 'recipients': recipients,
        'position': 0, 'sent': 0, 'failed': 0, 'status': 'running',
        'chat_id': CHAT.id, 'message_id': 9})

    saves = {'n': 0}
    original = c.db['broadcasts'].update_one

    async def flaky_save(*args, **kwargs):
        saves['n'] += 1
        if saves['n'] == 2:          # первая отметка прошла, вторая — нет
            raise RuntimeError('база отвалилась')
        return await original(*args, **kwargs)

    c.db['broadcasts'].update_one = flaky_save
    session.calls.clear()

    with pytest.raises(RuntimeError):
        await _run(c, bot, 'job-1')

    c.db['broadcasts'].update_one = original
    job = await c.db['broadcasts'].find_one({'_id': 'job-1'})
    # обработчик ошибки дописывает настоящую позицию, а не последнюю удачную
    assert job['status'] == 'failed'
    assert job['position'] == 10, 'позиция не сохранена — продолжить неоткуда'

    edits = [t for name, t in session.calls if name == 'EditMessageText']
    assert 'прервана' in edits[-1]


async def test_resume_continues_from_where_it_stopped(env):
    """Продолжение не начинает всё заново: повторно уйдёт не больше шага."""
    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', 10)
    for user_id in range(400, 430):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    from app.admin.broadcast import _recipients, _run

    recipients = await _recipients(c, {'growth.segment': 'expired_3d'})
    await c.db['broadcasts'].insert_one({
        '_id': 'job-2', 'text': 'Привет!', 'recipients': recipients,
        'position': 20, 'sent': 18, 'failed': 2, 'status': 'running',
        'chat_id': CHAT.id, 'message_id': 9})

    session.calls.clear()
    await _run(c, bot, 'job-2')

    delivered = [t for name, t in session.calls if t == 'Привет!']
    assert len(delivered) == 10, 'должны уйти только оставшиеся десять'

    job = await c.db['broadcasts'].find_one({'_id': 'job-2'})
    assert job['status'] == 'done' and job['sent'] == 28


async def test_restart_marks_the_broadcast_interrupted(env):
    """Задача живёт в памяти: перезапуск бота обрывает её без следов.
    Отметка при старте — единственный способ об этом узнать."""
    from app.admin.broadcast import mark_interrupted

    dp, bot, session, c = env
    await c.db['broadcasts'].insert_one({
        '_id': 'job-3', 'text': 'x', 'recipients': [1, 2, 3],
        'position': 1, 'sent': 1, 'failed': 0, 'status': 'running',
        'chat_id': CHAT.id, 'message_id': 9})

    interrupted = await mark_interrupted(c)

    assert [job['_id'] for job in interrupted] == ['job-3']
    assert (await c.db['broadcasts'].find_one({'_id': 'job-3'}))['status'] == 'interrupted'


async def test_a_finished_broadcast_is_not_touched_on_restart(env):
    from app.admin.broadcast import mark_interrupted

    dp, bot, session, c = env
    await c.db['broadcasts'].insert_one({'_id': 'job-4', 'status': 'done'})

    assert await mark_interrupted(c) == []


async def test_recipients_are_read_before_sending(env):
    """Курсор, открытый на всё время отправки, сервер закрывает по таймауту —
    именно так рассылка обрывалась на середине. Список читается заранее."""
    from app.admin.broadcast import _recipients

    dp, bot, session, c = env
    for user_id in range(500, 505):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    assert await _recipients(c, {'growth.segment': 'expired_3d'}) == list(range(500, 505))


# ── заблокировавшие бота ────────────────────────────────────────────────────
#
# Хук on_blocked в Sender был, но его никто не передавал: отметка не
# ставилась, и каждая рассылка заново тратила попытку на тех, кому уже
# нельзя писать. «Не доставлено» росло без объяснения.

async def test_blocking_the_bot_is_remembered(env):
    from aiogram.exceptions import TelegramForbiddenError

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    for user_id in (600, 601, 602):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    original = bot.session.make_request

    async def blocked_601(bot_, method, timeout=None):
        if type(method).__name__ == 'SendMessage' and method.chat_id == 601:
            raise TelegramForbiddenError(method=method, message='bot was blocked')
        return await original(bot_, method, timeout)

    bot.session.make_request = blocked_601
    await run_broadcast(c, bot, {'growth.segment': 'expired_3d'}, 'Привет!')

    assert (await c.users.get(601))['growth']['blocked_bot'] is True
    assert 'blocked_bot' not in (await c.users.get(600)).get('growth', {})


async def test_the_next_broadcast_skips_them(env):
    from app.admin.broadcast import _recipients

    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 700},
                          'growth': {'segment': 'expired_3d'}})
    await c.users.create({'user_data': {'user_id': 701},
                          'growth': {'segment': 'expired_3d', 'blocked_bot': True}})

    assert await _recipients(c, {'growth.segment': 'expired_3d'}) == [700]


async def test_clearing_the_list_brings_them_back(env):
    """Telegram не сообщает о разблокировке — узнать можно только попыткой.
    Поэтому список чистится руками, и после этого письмо снова пойдёт."""
    from app.admin.broadcast import _recipients

    dp, bot, session, c = env
    await c.users.create({'user_data': {'user_id': 702},
                          'growth': {'segment': 'expired_3d', 'blocked_bot': True}})

    assert await c.users.blocked_count() == 1
    assert await c.users.unmark_blocked() == 1

    assert await c.users.blocked_count() == 0
    assert await _recipients(c, {'growth.segment': 'expired_3d'}) == [702]


async def test_blocked_are_counted_for_the_admin(env):
    dp, bot, session, c = env
    for user_id, blocked in ((800, True), (801, True), (802, False)):
        growth = {'segment': 'expired_3d'}
        if blocked:
            growth['blocked_bot'] = True
        await c.users.create({'user_data': {'user_id': user_id}, 'growth': growth})

    assert await c.users.blocked_count() == 2
    assert await c.users.blocked_count({'growth.segment': 'active_paid'}) == 0


# ── флуд-пауза Telegram ─────────────────────────────────────────────────────
#
# На 190 000 писем Telegram начинает отвечать 429 и просить подождать. Пауза
# бывает в полчаса, и со стороны это неотличимо от зависшей рассылки: счётчик
# замер, лог молчит, кнопок нет.

async def test_a_flood_pause_is_shown_not_hidden(env):
    from aiogram.exceptions import TelegramRetryAfter

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', 100)
    for user_id in range(900, 903):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    original = bot.session.make_request
    hit = {'n': 0}

    async def flood_once(bot_, method, timeout=None):
        if type(method).__name__ == 'SendMessage' and method.chat_id == 901:
            hit['n'] += 1
            if hit['n'] == 1:
                raise TelegramRetryAfter(method=method, message='flood',
                                         retry_after=0)
        return await original(bot_, method, timeout)

    bot.session.make_request = flood_once
    session.calls.clear()
    await run_broadcast(c, bot, {'growth.segment': 'expired_3d'}, 'Привет!')

    edits = [t for name, t in session.calls if name == 'EditMessageText']
    assert any('Пауза по требованию Telegram' in t for t in edits), edits


async def test_a_flood_pause_does_not_count_as_undelivered(env):
    """Раньше три подряд флуд-паузы записывали живого человека в «не
    доставлено»: пауза тратила попытку, хотя отказа не было."""
    from aiogram.exceptions import TelegramRetryAfter

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.users.create({'user_data': {'user_id': 910},
                          'growth': {'segment': 'expired_3d'}})

    original = bot.session.make_request
    tries = {'n': 0}

    async def flood_thrice(bot_, method, timeout=None):
        if type(method).__name__ == 'SendMessage':
            tries['n'] += 1
            if tries['n'] <= 3:
                raise TelegramRetryAfter(method=method, message='flood',
                                         retry_after=0)
        return await original(bot_, method, timeout)

    bot.session.make_request = flood_thrice
    job = await run_broadcast(c, bot, {'growth.segment': 'expired_3d'}, 'Привет!')

    assert job['sent'] == 1 and job['failed'] == 0


async def test_a_running_broadcast_can_be_stopped(env):
    """190 тысяч писем идут часами. Без кнопки единственный способ
    прекратить — перезапустить процесс."""
    from app.admin.broadcast import _recipients, _run

    dp, bot, session, c = env
    await c.settings.set('campaign.broadcast_delay_ms', 0)
    await c.settings.set('campaign.broadcast_progress_step', 5)
    for user_id in range(920, 950):
        await c.users.create({'user_data': {'user_id': user_id},
                              'growth': {'segment': 'expired_3d'}})

    recipients = await _recipients(c, {'growth.segment': 'expired_3d'})
    await c.db['broadcasts'].insert_one({
        '_id': 'job-stop', 'text': 'Привет!', 'recipients': recipients,
        'position': 0, 'sent': 0, 'failed': 0, 'status': 'running',
        'chat_id': CHAT.id, 'message_id': 9})

    original = c.db['broadcasts'].find_one

    async def stop_after_first_step(query, projection=None):
        doc = await original(query, projection)
        # имитируем нажатие «Остановить» сразу после первой отметки
        if projection == {'status': 1}:
            return {'status': 'stopping'}
        return doc

    c.db['broadcasts'].find_one = stop_after_first_step
    await _run(c, bot, 'job-stop')
    c.db['broadcasts'].find_one = original

    job = await c.db['broadcasts'].find_one({'_id': 'job-stop'})
    assert job['status'] == 'interrupted'
    assert job['position'] == 5, 'остановились на ближайшем шаге, а не в конце'

    delivered = [t for name, t in session.calls if t == 'Привет!']
    assert len(delivered) == 5, 'после остановки писать не должны'


def test_eta_is_human_readable():
    from app.admin.broadcast import eta

    assert eta(100, 0.04) == '4 сек'
    assert eta(10_000, 0.04) == '6 мин'
    assert eta(190_000, 0.04) == '2.1 ч'
