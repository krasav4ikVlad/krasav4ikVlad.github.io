"""Партнёры: своя ссылка, свой чат уведомлений, свои правила триала.

Смысл раздела в двух вещах, и обе проверяются здесь. Первая: аудитория
партнёра пришла с его площадки и подписываться на наш канал ради трёх
пробных дней не станет — значит, требование подписки должно сниматься
именно для его людей и ни для кого больше. Вторая: события по его людям
нужно видеть отдельно, а не вылавливать из общей ленты.
"""

from datetime import datetime, timedelta

import pytest
from aiogram.types import Chat, Message, Update, User

from app.admin import partners
from app.bot.callbacks import Admin as Adm
from app.core.time import now
from app.repositories.ref_tags import RefTagsRepository
from app.repositories.users import UsersRepository
from app.services.notifier import Notifier
from app.services.trial import TrialService
from app.settings.service import SettingsService

from tests.test_admin_panel import (ADMIN, CHAT, admin_env, callback,  # noqa: F401
                                    message)


@pytest.fixture
def tags(db):
    return RefTagsRepository(db['ref_tags'])


@pytest.fixture
def users(db):
    return UsersRepository(db['users'])


@pytest.fixture
def settings(db):
    return SettingsService(db['bot_settings'])


class FakeBot:
    """Запоминает, куда и что ушло."""

    def __init__(self, broken: bool = False):
        self.sent: list[dict] = []
        self.broken = broken

    async def send_message(self, chat_id, text, message_thread_id=None, **kw):
        if self.broken:
            raise RuntimeError('chat not found')
        self.sent.append({'chat_id': chat_id, 'topic': message_thread_id,
                          'text': text})
        return Message(message_id=1, date=datetime.now(),
                       chat=Chat(id=chat_id, type='supergroup'), text=text)

    async def get_chat_member(self, chat_id, user_id):
        return type('M', (), {'status': 'left'})()


async def person(users, user_id: int, tag: str = '') -> dict:
    doc = {'user_data': {'user_id': user_id, 'username': f'u{user_id}'},
           'info': {'balance': 0}, 'growth': {}, 'vpn': {'shortUuid': ''}}
    if tag:
        doc['user_data']['ref_tag'] = tag
    await users.create(doc)
    return await users.get(user_id)


# ── триал без подписки на канал ─────────────────────────────────────────────
async def test_a_partner_referral_does_not_need_the_channel(tags, users, settings, db):
    """Его аудитория пришла с его площадки: требовать подписки на наш канал
    значит потерять её на первом же шаге."""
    await tags.create('vlad', 500)
    await tags.update('vlad', no_channel=True)
    trial = TrialService(users, settings, vpn=None, ref_tags=tags)

    assert await trial.needs_channel(await person(users, 1, tag='vlad')) is False


async def test_everyone_else_still_needs_the_channel(tags, users, settings, db):
    await tags.create('vlad', 500)
    await tags.update('vlad', no_channel=True)
    trial = TrialService(users, settings, vpn=None, ref_tags=tags)

    assert await trial.needs_channel(await person(users, 2)) is True


async def test_a_partner_without_the_exemption_needs_it_too(tags, users, settings, db):
    await tags.create('vlad', 500)
    trial = TrialService(users, settings, vpn=None, ref_tags=tags)

    assert await trial.needs_channel(await person(users, 3, tag='vlad')) is True


async def test_the_general_switch_still_wins_when_it_is_off(tags, users, settings, db):
    """Выключенное требование подписки не должно включаться обратно ни для
    кого — в том числе для партнёрских."""
    await settings.set('trial.require_subscription', False)
    trial = TrialService(users, settings, vpn=None, ref_tags=tags)

    assert await trial.needs_channel(await person(users, 4)) is False


async def test_the_trial_is_given_without_a_subscription(tags, users, settings, db):
    """Главная проверка: не только экран, но и сама выдача."""
    class Panel:
        async def create_subscription(self, user_id, days):
            return {'uuid': 'u-1', 'shortUuid': 's-1',
                    'expireAt': now() + timedelta(days=days),
                    'createdAt': now()}

    await tags.create('vlad', 500)
    await tags.update('vlad', no_channel=True)
    await person(users, 1, tag='vlad')
    trial = TrialService(users, settings, vpn=Panel(), bot=FakeBot(),
                         ref_tags=tags)

    result = await trial.claim(1)

    assert result.ok is True


async def test_without_the_exemption_the_trial_is_refused(tags, users, settings, db):
    await tags.create('vlad', 500)
    await person(users, 1, tag='vlad')
    trial = TrialService(users, settings, vpn=None, bot=FakeBot(), ref_tags=tags)

    assert (await trial.claim(1)).reason == 'not_subscribed'


# ── уведомления по людям партнёра ───────────────────────────────────────────
async def test_the_partner_events_go_to_their_own_chat(tags, users, settings, db):
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(1, username='u1', utm='ref_vlad')

    partner_copy = [row for row in bot.sent if row['chat_id'] == -100123]
    assert len(partner_copy) == 1
    assert partner_copy[0]['topic'] == 42 and 'vlad' in partner_copy[0]['text']


async def test_a_topup_reaches_the_partner_chat_too(tags, users, settings, db):
    """Регистрация без денег партнёру мало что говорит."""
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.topup(1, amount=500, bonus=100, credit=600, provider='wata')

    assert [row for row in bot.sent if row['chat_id'] == -100123]


async def test_nothing_extra_is_sent_without_a_partner_chat(tags, users, settings, db):
    await tags.create('vlad', 500)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(1, username='u1')

    assert all(row['chat_id'] != -100123 for row in bot.sent)


async def test_someone_elses_events_do_not_reach_the_partner(tags, users, settings, db):
    """Иначе в партнёрском чате окажется вся база."""
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 2)
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(2, username='u2')

    assert not [row for row in bot.sent if row['chat_id'] == -100123]


async def test_a_broken_partner_chat_does_not_break_the_event(tags, users,
                                                              settings, db):
    """Копия уведомления не должна ронять ни событие, ни общий админ-чат."""
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')
    notifier = Notifier(FakeBot(broken=True), settings, users, ref_tags=tags)

    assert await notifier.registered(1, username='u1') is False   # и не упало


# ── разбор чата ─────────────────────────────────────────────────────────────
def test_the_chat_and_topic_are_read():
    assert partners.parse_chat('-1001234567890 42') == (-1001234567890, 42)
    assert partners.parse_chat('-1001234567890') == (-1001234567890, 0)
    assert partners.parse_chat('  ') is None
    assert partners.parse_chat('не число') is None


# ── админка ─────────────────────────────────────────────────────────────────
async def test_the_section_lists_partners(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='partners').pack()))

    assert 'Партнёры' in session.last_text and 'vlad' in str(session.markups[-1])


async def test_a_partner_is_created_from_the_panel(admin_env):
    dp, bot, session, c = admin_env
    await c.users.create({'user_data': {'user_id': 777, 'username': 'blogger'}})

    await dp.feed_update(bot, callback(Adm(act='pnew').pack()))
    await dp.feed_update(bot, message('vlad 777 блогер с ютуба'))

    assert await c.ref_tags.owner('vlad') == 777
    assert 'заведён' in session.last_text


async def test_the_channel_requirement_is_switched_off_by_a_button(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pchan', a='vlad').pack()))

    assert (await c.ref_tags.get('vlad'))['no_channel'] is True


async def test_the_switch_works_both_ways(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pchan', a='vlad').pack()))
    await dp.feed_update(bot, callback(Adm(act='pchan', a='vlad').pack()))

    assert (await c.ref_tags.get('vlad'))['no_channel'] is False


async def test_the_chat_is_checked_by_sending_not_by_faith(admin_env):
    """Неправильный id тихо превратил бы весь партнёрский поток в строчки
    в логе — поэтому проверяем отправкой."""
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    async def broken(*args, **kwargs):
        raise RuntimeError('chat not found')

    await dp.feed_update(bot, callback(Adm(act='pchat', a='vlad').pack()))
    bot.send_message = broken
    await dp.feed_update(bot, message('-100999 42'))

    assert not (await c.ref_tags.get('vlad')).get('chat_id')


async def test_a_working_chat_is_saved(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pchat', a='vlad').pack()))
    await dp.feed_update(bot, message('-100999 42'))

    partner = await c.ref_tags.get('vlad')
    assert partner['chat_id'] == -100999 and partner['topic_id'] == 42


async def test_the_chat_can_be_taken_away(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)
    await c.ref_tags.update('vlad', chat_id=-100999, topic_id=42)

    await dp.feed_update(bot, callback(Adm(act='pchatoff', a='vlad').pack()))

    assert not (await c.ref_tags.get('vlad'))['chat_id']


async def test_a_tag_is_deleted_only_after_a_confirmation(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pdel', a='vlad').pack()))
    assert await c.ref_tags.get('vlad') is not None

    await dp.feed_update(bot, callback(Adm(act='pdelok', a='vlad').pack()))
    assert await c.ref_tags.get('vlad') is None


# ── в оба чата, а не в один ─────────────────────────────────────────────────
#
# Партнёрское событие — обычное событие бота, и в общей ленте ему место:
# админ-чат остаётся полным. Чат партнёра нужен затем, что полтора десятка
# его человек в день теряются среди полутора тысяч чужих.

async def test_the_event_reaches_both_chats(tags, users, settings, db):
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.topup(1, amount=500, bonus=0, credit=500, provider='wata')

    assert [row for row in bot.sent if row['chat_id'] == -100777]   # админ-чат
    assert [row for row in bot.sent if row['chat_id'] == -100123]   # партнёр


async def test_the_admin_chat_says_which_partner_it_was(tags, users, settings, db):
    """Иначе партнёрскую покупку не отличить в ленте от любой другой."""
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.subscription_created(1, {'title': 'Месяц'}, {})

    admin_copy = next(row for row in bot.sent if row['chat_id'] == -100777)
    assert 'vlad' in admin_copy['text']


async def test_the_tag_is_marked_even_without_a_partner_chat(tags, users,
                                                             settings, db):
    """Чат партнёру можно и не заводить — но в ленте метку видеть полезно."""
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 500)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(1, username='u1')

    assert 'vlad' in bot.sent[0]['text'] and bot.sent[0]['chat_id'] == -100777


async def test_a_dead_partner_chat_does_not_eat_the_admin_notice(tags, users,
                                                                 settings, db):
    """Копия — дело второе: её неудача не должна лишать вас события."""
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')

    class HalfBroken(FakeBot):
        async def send_message(self, chat_id, text, message_thread_id=None, **kw):
            if chat_id == -100123:
                raise RuntimeError('chat not found')
            return await FakeBot.send_message(self, chat_id, text,
                                              message_thread_id, **kw)

    bot = HalfBroken()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    assert await notifier.registered(1, username='u1') is True
    assert [row for row in bot.sent if row['chat_id'] == -100777]


async def test_an_ordinary_user_gets_no_tag_line(tags, users, settings, db):
    await settings.set('notify.chat_id', -100777)
    await person(users, 2)
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(2, username='u2')

    assert 'Метка' not in bot.sent[0]['text']


# ── регистрации и оплаты — по разным темам ──────────────────────────────────
#
# В одной ленте оплаты тонут среди заходов: людей приходит вдесятеро
# больше, чем платит. Разделение тем — это возможность смотреть только на
# то, что приносит деньги.

def test_the_topic_depends_on_the_kind_of_event():
    from app.services.notifier import partner_topic

    partner = {'topic_id': 42, 'topic_pay_id': 43}

    assert partner_topic(partner, paid=False) == 42
    assert partner_topic(partner, paid=True) == 43


def test_without_a_payments_topic_everything_goes_to_the_first():
    """Одна лента лучше потерянного события."""
    from app.services.notifier import partner_topic

    assert partner_topic({'topic_id': 42}, paid=True) == 42


def test_the_chat_itself_works_without_any_topics():
    from app.services.notifier import partner_topic

    assert partner_topic({}, paid=True) == 0


async def test_a_registration_goes_to_the_first_topic(tags, users, settings, db):
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42, topic_pay_id=43)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.registered(1, username='u1')

    copy = next(row for row in bot.sent if row['chat_id'] == -100123)
    assert copy['topic'] == 42


async def test_a_payment_goes_to_the_second(tags, users, settings, db):
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42, topic_pay_id=43)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.topup(1, amount=500, bonus=0, credit=500, provider='wata')

    copy = next(row for row in bot.sent if row['chat_id'] == -100123)
    assert copy['topic'] == 43


# Списания с баланса партнёру не копируются вовсе — ни покупка, ни
# автопродление. Это не пришедшие деньги: те же рубли он уже видел
# пополнением, и процент с них ему уже посчитан. Автопродление вдобавок
# идёт каждый месяц само и заполнило бы тему оплат целиком.

async def test_a_purchase_does_not_reach_the_partner_chat(tags, users, settings, db):
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42, topic_pay_id=43)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.subscription_created(1, {'title': 'Месяц'}, {})

    assert not [row for row in bot.sent if row['chat_id'] == -100123]


async def test_an_autorenewal_does_not_reach_the_partner_chat(tags, users,
                                                              settings, db):
    """Главная причина: оно приходит каждый месяц само, и в теме оплат
    вместо денег партнёра оказывается лента списаний."""
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42, topic_pay_id=43)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.renewed(1, {'title': 'Месяц'}, price=200)

    assert not [row for row in bot.sent if row['chat_id'] == -100123]


async def test_the_admin_chat_still_gets_the_autorenewal(tags, users, settings, db):
    """Из общей ленты оно никуда не делось — там ему место."""
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.renewed(1, {'title': 'Месяц'}, price=200)

    admin_copy = next(row for row in bot.sent if row['chat_id'] == -100777)
    assert 'Автопродление' in admin_copy['text'] and 'vlad' in admin_copy['text']


async def test_a_topup_still_reaches_the_payments_topic(tags, users, settings, db):
    """Ради чего тема и заводилась."""
    await tags.create('vlad', 500)
    await tags.update('vlad', chat_id=-100123, topic_id=42, topic_pay_id=43)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.topup(1, amount=500, bonus=0, credit=500, provider='wata')

    copy = next(row for row in bot.sent if row['chat_id'] == -100123)
    assert copy['topic'] == 43 and 'Пополнение' in copy['text']


# ── кому засчитан приведённый ───────────────────────────────────────────────
async def test_the_event_names_who_gets_the_referral(tags, users, settings, db):
    """По одной метке не видно, на чей счёт идут проценты."""
    await settings.set('notify.chat_id', -100777)
    await tags.create('vlad', 7996131040)
    await tags.update('vlad', chat_id=-100123)
    await person(users, 1, tag='vlad')
    bot = FakeBot()
    notifier = Notifier(bot, settings, users, ref_tags=tags)

    await notifier.topup(1, amount=500, bonus=0, credit=500, provider='wata')

    for row in bot.sent:
        assert '7996131040' in row['text']


# ── админка: вторая тема ────────────────────────────────────────────────────
async def test_the_payments_topic_is_saved(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)
    await c.ref_tags.update('vlad', chat_id=-100999, topic_id=42)

    await dp.feed_update(bot, callback(Adm(act='pchatpay', a='vlad').pack()))
    await dp.feed_update(bot, message('43'))

    partner = await c.ref_tags.get('vlad')
    assert partner['topic_pay_id'] == 43 and partner['topic_id'] == 42


async def test_the_payments_topic_needs_a_chat_first(admin_env):
    """Тема без чата — это ничего: класть событие некуда."""
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pchatpay', a='vlad').pack()))
    await dp.feed_update(bot, message('43'))

    assert not (await c.ref_tags.get('vlad')).get('topic_pay_id')


async def test_zero_returns_payments_to_the_first_topic(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)
    await c.ref_tags.update('vlad', chat_id=-100999, topic_id=42, topic_pay_id=43)

    await dp.feed_update(bot, callback(Adm(act='pchatpay', a='vlad').pack()))
    await dp.feed_update(bot, message('0'))

    assert (await c.ref_tags.get('vlad'))['topic_pay_id'] == 0


async def test_removing_the_chat_removes_both_topics(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)
    await c.ref_tags.update('vlad', chat_id=-100999, topic_id=42, topic_pay_id=43)

    await dp.feed_update(bot, callback(Adm(act='pchatoff', a='vlad').pack()))

    partner = await c.ref_tags.get('vlad')
    assert not partner['chat_id'] and not partner['topic_pay_id']


# ── старые ссылки из настроек ───────────────────────────────────────────────
async def test_old_aliases_are_shown_on_the_screen(admin_env):
    """Они работают, но живут мимо этого экрана: ни счётчика, ни настроек.
    Молчать о них — значит однажды искать «куда делась старая ссылка»."""
    dp, bot, session, c = admin_env
    await c.settings.set('link.ref_aliases', 'oldvlad:7996131040')

    await dp.feed_update(bot, callback(Adm(act='partners').pack()))

    assert 'oldvlad' in session.last_text and '7996131040' in session.last_text


async def test_a_broken_alias_line_does_not_break_the_screen(admin_env):
    dp, bot, session, c = admin_env
    await c.settings.set('link.ref_aliases', 'мусор, без:двоеточия_цифр')

    await dp.feed_update(bot, callback(Adm(act='partners').pack()))

    assert 'Партнёры' in session.last_text


# ── повышенный процент на срок ──────────────────────────────────────────────
#
# «Эту неделю у тебя 50 вместо 30». Срок обязателен: повышенный процент без
# даты окончания однажды забывают снять, и он тихо становится постоянным.

def test_the_boost_works_only_while_it_lasts():
    from datetime import timedelta

    from app.domain.ref_tags import boost_rate

    live = {'boost_rate': 0.5, 'boost_until': now() + timedelta(days=1)}
    dead = {'boost_rate': 0.5, 'boost_until': now() - timedelta(days=1)}

    assert boost_rate(live, now()) == 0.5
    assert boost_rate(dead, now()) == 0.0


def test_a_boost_without_a_deadline_does_not_count():
    """Иначе он остаётся навсегда, и никто об этом не помнит."""
    from app.domain.ref_tags import boost_rate

    assert boost_rate({'boost_rate': 0.5}, now()) == 0.0


def test_an_ordinary_partner_has_no_boost():
    from app.domain.ref_tags import boost_rate

    assert boost_rate({}, now()) == 0.0


async def test_the_referrer_gets_the_raised_percent(db, tags, users, settings):
    """Главная проверка: деньги считаются по повышенной ставке."""
    from datetime import timedelta

    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    await tags.create('vlad', 500)
    await tags.update('vlad', boost_rate=0.5,
                      boost_until=now() + timedelta(days=7))
    await users.create({'user_data': {'user_id': 500},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await users.create({'user_data': {'user_id': 1, 'referrer': 500,
                                      'ref_tag': 'vlad'},
                        'info': {'balance': 0}, 'growth': {}})

    topup = TopupService(users, PaymentsRepository(db['payments']), settings,
                         ref_tags=tags)
    result = await topup.process(provider='wata', txid='t1', amount=1000,
                                 user_id=1)

    assert result['referral_reward'] == 500      # 50%, а не обычные 30%


async def test_an_expired_boost_pays_the_usual_percent(db, tags, users, settings):
    from datetime import timedelta

    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    await tags.create('vlad', 500)
    await tags.update('vlad', boost_rate=0.5,
                      boost_until=now() - timedelta(days=1))
    await users.create({'user_data': {'user_id': 500},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await users.create({'user_data': {'user_id': 1, 'referrer': 500,
                                      'ref_tag': 'vlad'},
                        'info': {'balance': 0}, 'growth': {}})

    topup = TopupService(users, PaymentsRepository(db['payments']), settings,
                         ref_tags=tags)
    result = await topup.process(provider='wata', txid='t2', amount=1000,
                                 user_id=1)

    assert result['referral_reward'] == 300


async def test_someone_without_a_tag_is_unaffected(db, tags, users, settings):
    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    await users.create({'user_data': {'user_id': 500},
                        'info': {'balance': 0, 'ref_stats': {'referrals': []}}})
    await users.create({'user_data': {'user_id': 1, 'referrer': 500},
                        'info': {'balance': 0}, 'growth': {}})

    topup = TopupService(users, PaymentsRepository(db['payments']), settings,
                         ref_tags=tags)
    result = await topup.process(provider='wata', txid='t3', amount=1000,
                                 user_id=1)

    assert result['referral_reward'] == 300


async def test_the_boost_is_set_from_the_panel(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pboost', a='vlad').pack()))
    await dp.feed_update(bot, message('50 7'))

    partner = await c.ref_tags.get('vlad')
    assert partner['boost_rate'] == 0.5 and partner['boost_until']


async def test_a_percent_below_the_usual_is_refused(admin_env):
    """«Повышенный» 20% при обычных 30 ничего не повышает и только путает."""
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pboost', a='vlad').pack()))
    await dp.feed_update(bot, message('20 7'))

    assert not (await c.ref_tags.get('vlad')).get('boost_rate')
    assert 'должен быть больше' in session.last_text


async def test_the_boost_needs_a_deadline(admin_env):
    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)

    await dp.feed_update(bot, callback(Adm(act='pboost', a='vlad').pack()))
    await dp.feed_update(bot, message('50'))

    assert not (await c.ref_tags.get('vlad')).get('boost_rate')


async def test_the_boost_can_be_taken_away(admin_env):
    from datetime import timedelta

    dp, bot, session, c = admin_env
    await c.ref_tags.create('vlad', ADMIN.id)
    await c.ref_tags.update('vlad', boost_rate=0.5,
                            boost_until=now() + timedelta(days=7))

    await dp.feed_update(bot, callback(Adm(act='pboost', a='vlad').pack()))

    assert not (await c.ref_tags.get('vlad'))['boost_rate']
