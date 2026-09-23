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


async def test_a_purchase_reaches_the_partner_chat_too(tags, users, settings, db):
    """Регистрация без покупки партнёру мало что говорит."""
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
