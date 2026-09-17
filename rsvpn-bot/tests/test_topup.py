"""Зачисление денег: идемпотентность, бонусы, рефералка."""

import pytest

from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services.topup import TopupService
from app.settings.service import SettingsService


@pytest.fixture
def topup(db):
    return TopupService(
        UsersRepository(db['users']),
        PaymentsRepository(db['payments']),
        SettingsService(db['bot_settings']),
    )


async def balance(db, user_id: int) -> int:
    doc = await db['users'].find_one({'user_data.user_id': user_id})
    return doc['info']['balance']


async def test_credits_amount_with_bonus(db, user_factory, topup):
    await user_factory()
    result = await topup.process(provider='wata', txid='t1', amount=100, user_id=1)

    # бонус за пополнение по умолчанию 20%
    assert result['status'] == 'ok' and result['bonus'] == 20
    assert await balance(db, 1) == 120


async def test_duplicate_webhook_credits_once(db, user_factory, topup):
    await user_factory()
    first = await topup.process(provider='wata', txid='same', amount=100, user_id=1)
    second = await topup.process(provider='wata', txid='same', amount=100, user_id=1)

    assert first['status'] == 'ok' and second['status'] == 'duplicate'
    assert await balance(db, 1) == 120


async def test_bonus_can_be_switched_off_from_admin(db, user_factory, topup):
    await user_factory()
    await topup.settings.set('bonus.topup_enabled', False)

    result = await topup.process(provider='wata', txid='t2', amount=100, user_id=1)
    assert result['bonus'] == 0
    assert await balance(db, 1) == 100


async def test_tribute_gets_extra_percent(db, user_factory, topup):
    await user_factory()
    result = await topup.process(provider='tribute', txid='t3', amount=100, user_id=1)
    assert result['bonus'] == 25          # 20% обычный + 5% за Tribute


async def test_personal_multiplier_is_consumed_once(db, user_factory, topup):
    """Раньше cards_ru считал этот бонус сам и передавал уже увеличенную сумму,
    из-за чего сверху накручивался ещё и общий бонус."""
    await user_factory(**{'info.bonus_multiplier': 0.5})

    first = await topup.process(provider='cards_ru', txid='p1', amount=100, user_id=1)
    second = await topup.process(provider='cards_ru', txid='p2', amount=100, user_id=1)

    assert first['bonus'] == 70           # 20% + персональные 50%
    assert second['bonus'] == 20          # множитель израсходован
    assert await balance(db, 1) == 170 + 120


async def test_referrer_gets_percent_of_base_amount(db, user_factory, topup):
    await user_factory()                                   # user_id=1 — реферер
    await user_factory(**{'user_data.referrer': 1})        # user_id=2 — друг

    result = await topup.process(provider='wata', txid='r1', amount=500, user_id=2)

    assert result['referral_reward'] == 150                # 30% от 500, не от 600
    referrer = await db['users'].find_one({'user_data.user_id': 1})
    assert referrer['info']['ref_stats']['withdrawable'] == 150
    assert referrer['info']['ref_stats']['paying_referrals'] == [2]


async def test_referrals_can_be_disabled(db, user_factory, topup):
    await user_factory()
    await user_factory(**{'user_data.referrer': 1})
    await topup.settings.set('features.referrals_enabled', False)

    result = await topup.process(provider='wata', txid='r2', amount=500, user_id=2)
    assert result['referral_reward'] == 0


async def test_ab_bonus_applies_once_to_new_trial(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'new_trial_d2', 'growth.ab_group': 'bonus_30'})

    first = await topup.process(provider='wata', txid='ab1', amount=100, user_id=1)
    second = await topup.process(provider='wata', txid='ab2', amount=100, user_id=1)

    assert first['ab_bonus'] == 30
    assert second['ab_bonus'] == 0
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['growth']['ab_group'] == 'used'
    assert user['campaigns']['converted_from'] == 'bonus_30'


async def test_ab_bonus_not_given_outside_new_trial(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'active_paid', 'growth.ab_group': 'bonus_30'})
    result = await topup.process(provider='wata', txid='ab3', amount=100, user_id=1)
    assert result['ab_bonus'] == 0


async def test_campaign_conversion_is_recorded(db, user_factory, topup):
    await user_factory(**{'growth.segment': 'expired_7d', 'campaigns.expired_7d_sent': 'x'})
    await topup.process(provider='wata', txid='c1', amount=200, user_id=1)

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['campaigns']['expired_converted_from'] == 'expired_7d'
    assert user['campaigns']['expired_converted_amount'] == 200


async def test_unknown_user_does_not_crash(db, topup):
    result = await topup.process(provider='wata', txid='u1', amount=100, user_id=999)
    assert result['status'] == 'user_not_found'


async def test_bad_input_is_rejected(db, user_factory, topup):
    await user_factory()
    assert (await topup.process(provider='wata', txid='x', amount=0, user_id=1))['status'] == 'bad_amount'
    assert (await topup.process(provider='wata', txid='', amount=10, user_id=1))['status'] == 'no_txid'
    assert (await topup.process(provider='wata', txid='y', amount=10, user_id=None))['status'] == 'unknown_user'


# ── человек должен узнать, что деньги дошли ─────────────────────────────────
#
# Зачисление приходит вебхуком: человек в этот момент смотрит на страницу
# платёжки и видит только её «оплачено». Раньше бот сообщал о пополнении
# админам и молчал самому плательщику — со стороны это выглядело как
# «деньги ушли, бот не отреагировал».

class RecordingBot:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, user_id, text, reply_markup=None, **kwargs):
        if self.fail:
            raise RuntimeError('бот заблокирован пользователем')
        self.sent.append({'user_id': user_id, 'text': text, 'markup': reply_markup})
        return True


@pytest.fixture
def talking(db):
    service = TopupService(
        UsersRepository(db['users']),
        PaymentsRepository(db['payments']),
        SettingsService(db['bot_settings']),
    )
    service.bot = RecordingBot()
    return service


async def test_payer_gets_a_message(db, user_factory, talking):
    await user_factory()

    await talking.process(provider='wata', txid='t1', amount=100, user_id=1)

    sent = talking.bot.sent[0]
    assert sent['user_id'] == 1
    assert 'Баланс пополнен' in sent['text']
    assert '120₽' in sent['text']          # зачислено с бонусом
    assert sent['markup'] is not None      # кнопка «Моя подписка»


async def test_the_message_shows_the_bonus_separately(db, user_factory, talking):
    await user_factory()

    await talking.process(provider='wata', txid='t1', amount=100, user_id=1)
    text = talking.bot.sent[0]['text']

    assert '100₽' in text and '+20₽' in text


async def test_without_a_bonus_the_message_is_short(db, user_factory, talking):
    await user_factory()
    await talking.settings.set('bonus.topup_enabled', False)

    await talking.process(provider='wata', txid='t1', amount=100, user_id=1)
    text = talking.bot.sent[0]['text']

    assert 'Бонус' not in text
    assert '100₽' in text


async def test_referrer_is_told_about_the_reward(db, user_factory, talking):
    await user_factory()                                   # id 1 — пригласивший
    await user_factory(**{'user_data.referrer': 1})         # id 2 — друг

    await talking.process(provider='wata', txid='t1', amount=100, user_id=2)

    to_referrer = [s for s in talking.bot.sent if s['user_id'] == 1]
    assert to_referrer, 'пригласившему не сказали о начислении'
    assert '30₽' in to_referrer[0]['text']                  # 30% от 100₽


async def test_a_blocked_user_does_not_break_the_topup(db, user_factory):
    """Заблокировавший бота вполне может оплатить по старой ссылке."""
    service = TopupService(
        UsersRepository(db['users']),
        PaymentsRepository(db['payments']),
        SettingsService(db['bot_settings']),
    )
    service.bot = RecordingBot(fail=True)
    await user_factory()

    result = await service.process(provider='wata', txid='t1', amount=100, user_id=1)

    assert result['status'] == 'ok'
    assert await balance(db, 1) == 120


async def test_topup_works_even_without_a_bot(db, user_factory, topup):
    """attach_bot забыт — деньги всё равно зачисляются, в логе предупреждение."""
    await user_factory()
    result = await topup.process(provider='wata', txid='t1', amount=100, user_id=1)

    assert result['status'] == 'ok'
    assert await balance(db, 1) == 120


async def test_the_message_goes_out_once_per_payment(db, user_factory, talking):
    await user_factory()

    await talking.process(provider='wata', txid='same', amount=100, user_id=1)
    await talking.process(provider='wata', txid='same', amount=100, user_id=1)

    assert len(talking.bot.sent) == 1
