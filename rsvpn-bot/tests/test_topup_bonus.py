"""Бонус к пополнению на экране пополнения.

Бонус начислялся в TopupService и нигде не показывался: включённый в
админке «+20% за пополнение» человек видел только постфактум, в сообщении
о зачислении. Экран говорил лишь про надбавку новичкам — то есть ровно тот,
кто мог бы пополнить из-за бонуса, о нём и не знал.

Главная проверка здесь — test_the_screen_promises_exactly_what_is_credited:
процент на экране и рубли в кнопке считаются отдельно от начисления, и
разойдись они, правым окажется человек, а не бот.
"""

import pytest

from app.bot.handlers import payments
from app.repositories.payments import PaymentsRepository
from app.repositories.users import UsersRepository
from app.services.topup import TopupService
from app.settings.service import SettingsService


@pytest.fixture
async def settings(db):
    return SettingsService(db['bot_settings'])


@pytest.fixture
def topup(db):
    return TopupService(UsersRepository(db['users']),
                        PaymentsRepository(db['payments']),
                        SettingsService(db['bot_settings']))


def newcomer(**over) -> dict:
    return {'info': {}, 'growth': {'segment': 'new_trial_d1'}, **over}


async def line(settings, user: dict, provider: str = '', tribute: bool = False):
    return await payments.bonus_line(None, user, settings, provider, tribute)


# ── строка на экране ────────────────────────────────────────────────────────
async def test_the_ordinary_topup_bonus_is_written_on_the_screen(settings):
    """Тот самый случай: бонус включён в админке, а экран про него молчал."""
    text = await line(settings, {'info': {}, 'growth': {}})

    assert '+20% сверху' in text


async def test_a_switched_off_bonus_is_not_promised(settings):
    await settings.set('bonus.topup_enabled', False)

    assert await line(settings, {'info': {}, 'growth': {}}) == ''


async def test_a_zero_rate_is_not_promised(settings):
    await settings.set('bonus.topup_rate', 0)

    assert await line(settings, {'info': {}, 'growth': {}}) == ''


async def test_the_newcomer_bonus_adds_up_with_the_ordinary_one(settings):
    """Новичку начисляют оба, значит и на экране должна стоять сумма."""
    text = await line(settings, newcomer())

    assert '+50% сверху' in text and 'для новых пользователей' in text


async def test_someone_who_already_spent_the_newcomer_bonus_sees_the_ordinary(settings):
    text = await line(settings, {'info': {},
                                 'growth': {'segment': 'new_trial_d1',
                                            'ab_group': 'used'}})

    assert '+20% сверху' in text and 'для новых пользователей' not in text


async def test_a_personal_multiplier_is_counted_too(settings):
    text = await line(settings, {'info': {'bonus_multiplier': 0.5}, 'growth': {}})

    assert '+70% сверху' in text


async def test_the_word_today_is_only_where_it_is_true(settings):
    """Общий бонус стоит всегда, и «сегодня» в нём — ложная срочность."""
    always = await line(settings, {'info': {}, 'growth': {}})
    once = await line(settings, newcomer())

    assert 'При пополнении вы получите' in always
    assert 'При пополнении сегодня' in once


async def test_the_tribute_surcharge_is_named_on_the_list_of_methods(settings):
    text = await line(settings, {'info': {}, 'growth': {}}, tribute=True)

    assert 'ещё <b>+5%</b>' in text


async def test_the_tribute_surcharge_is_inside_the_percent_when_chosen(settings):
    text = await line(settings, {'info': {}, 'growth': {}}, provider='tribute')

    assert '+25% сверху' in text


async def test_the_foreign_card_button_is_tribute_too(settings):
    text = await line(settings, {'info': {}, 'growth': {}}, provider='tribute_eu')

    assert '+25% сверху' in text


# ── рубли ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('amount, expected', [(75, 15), (150, 30), (1000, 200)])
async def test_the_sum_after_the_bonus_is_counted(settings, amount, expected):
    offer = await payments.bonus_offer({'info': {}, 'growth': {}}, settings)

    assert payments.bonus_for(amount, offer) == expected


async def test_the_screen_promises_exactly_what_is_credited(db, settings, topup,
                                                            user_factory):
    """Экран и зачисление округляют по-разному — и человек это заметит."""
    await settings.set('bonus.topup_rate', 0.17)
    doc = await user_factory(**{'growth.segment': 'new_trial_d1'})

    offer = await payments.bonus_offer(doc, settings)
    promised = payments.bonus_for(333, offer)
    result = await topup.process(provider='wata', txid='t1', amount=333, user_id=1)

    assert promised == result['bonus'] + result['ab_bonus']
    assert 333 + promised == result['credit']


# ── кнопки сумм ─────────────────────────────────────────────────────────────
class Provider:
    code = 'wata'
    title = 'СБП'
    min_amount = 50
    direct_url = ''


class State:
    def __init__(self):
        self.data: dict = {}

    async def set_state(self, state):
        pass

    async def update_data(self, **kw):
        self.data.update(kw)


class Event:
    """Сообщение, а не нажатие: render тогда просто отвечает текстом."""

    def __init__(self):
        self.markups: list = []
        self.texts: list[str] = []

    async def answer(self, text='', reply_markup=None, **kw):
        self.texts.append(text)
        self.markups.append(reply_markup)
        return True


class Container:
    def __init__(self, db):
        from app.repositories.users import UsersRepository

        self.users = UsersRepository(db['users'])
        self.payments = type('Reg', (), {'get': staticmethod(
            lambda code: Provider() if code == 'wata' else None)})()

    def media(self, key):
        return None


async def buttons(db, settings, user: dict) -> list[str]:
    event = Event()
    from app.bot.callbacks import Payment

    await payments.choose_amount(event, Payment(provider='wata'), Container(db),
                                 user, settings, State())
    markup = next(m for m in event.markups if m is not None)
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_the_buttons_show_what_will_land_on_the_balance(db, settings,
                                                              user_factory):
    """Процент в тексте приходится считать в уме, и этого никто не делает."""
    doc = await user_factory()

    assert '150₽ → 180₽' in await buttons(db, settings, doc)


async def test_without_a_bonus_the_buttons_stay_plain(db, settings, user_factory):
    await settings.set('bonus.topup_enabled', False)
    doc = await user_factory()

    labels = await buttons(db, settings, doc)
    assert '150₽' in labels and not any('→' in label for label in labels)


async def test_nothing_is_promised_to_someone_who_gets_nothing(db, settings, topup,
                                                               user_factory):
    await settings.set('bonus.topup_enabled', False)
    doc = await user_factory()

    offer = await payments.bonus_offer(doc, settings)
    result = await topup.process(provider='wata', txid='t2', amount=500, user_id=1)

    assert payments.bonus_for(500, offer) == 0 == result['bonus'] + result['ab_bonus']
    assert await payments.bonus_line(None, doc, settings) == ''
