"""Админ-уведомления.

Класса Notifier в проекте не было вовсе, а `container.notifier` оставался
None — из-за чего молча не работали все уведомления в админ-чат. Эти тесты
закрепляют, что он есть и что его отказ не ломает основной сценарий.
"""

import pytest

from app.services.notifier import Notifier
from app.services.payouts import PayoutService


class RecordingBot:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if self.fail:
            raise RuntimeError('чат недоступен')
        self.sent.append({'chat_id': chat_id, 'text': text,
                          'thread': kwargs.get('message_thread_id'),
                          'markup': reply_markup})
        return True


@pytest.fixture
def bot():
    return RecordingBot()


@pytest.fixture
def notifier(bot, container):
    return Notifier(bot, container.settings, container.users)


async def test_notification_goes_to_the_configured_chat_and_topic(notifier, bot, container):
    await container.settings.set('notify.chat_id', -100500)
    await container.settings.set('notify.topic_registration', 77)

    assert await notifier.registered(5, username='ivan', utm='vk')

    assert bot.sent[0]['chat_id'] == -100500
    assert bot.sent[0]['thread'] == 77
    assert '@ivan' in bot.sent[0]['text'] and 'vk' in bot.sent[0]['text']


async def test_toggle_switches_all_notifications_off(notifier, bot, container):
    await container.settings.set('notify.enabled', False)

    assert await notifier.topup(5, amount=100, bonus=20, credit=120, provider='heleket') is False
    assert bot.sent == []


async def test_a_broken_admin_chat_does_not_raise(container):
    """Уведомление не должно ронять покупку — деньги важнее отчёта о них."""
    notifier = Notifier(RecordingBot(fail=True), container.settings, container.users)

    assert await notifier.topup(5, amount=100, bonus=0, credit=100, provider='wata') is False


async def test_username_is_taken_from_the_database_when_not_passed(notifier, bot,
                                                                   user_factory):
    user = await user_factory(**{'user_data.username': 'petya'})

    await notifier.email_changed(user['user_data']['user_id'], email='p@example.com')

    assert '@petya' in bot.sent[0]['text']


async def test_payout_card_carries_requisites_and_buttons(notifier, bot, container):
    """Карточку собирает админский слой и отдаёт готовой: она знает про
    балансы и кнопки решения, а notifier — только про тему чата."""
    from app.admin.payouts import card_markup, card_text

    await container.users.create({
        'user_data': {'user_id': 5, 'username': 'ivan'},
        'info': {'balance': 50, 'ref_stats': {
            'withdrawable': 900,
            'method': [{'id': 'm1', 'type': 'sbp',
                        'data': {'fio': 'Иван', 'phone': '+79000000000',
                                 'bank': 'Сбербанк'}}],
            'payout_selected': 'm1'}}})

    await notifier.payout_requested(await card_text(container, 5),
                                    await card_markup(container, 5))

    card = bot.sent[0]
    assert '900₽' in card['text'] and 'Сбербанк' in card['text']
    assert card['markup'] is not None, 'без кнопок админ не сможет обработать заявку'


async def test_failed_notification_releases_the_payout_claim(container):
    """Иначе человек навсегда останется с «заявка уже в обработке»."""
    notifier = Notifier(RecordingBot(fail=True), container.settings, container.users)
    payouts = PayoutService(container.users, container.settings, notifier)
    await container.users.create({
        'user_data': {'user_id': 5},
        'info': {'balance': 0, 'ref_stats': {'withdrawable': 900}}})

    result = await payouts.request(5)
    assert result.ok

    sent = await notifier.payout_requested('Заявка на вывод')
    assert sent is False

    await payouts.cancel_request(5)
    assert (await payouts.request(5)).ok, 'после неудачной отправки заявку нельзя подать заново'


async def test_admin_chat_gets_plain_characters(notifier, bot, container):
    """Админ-чат — та же аварийная поверхность, что и админка: сообщение с
    кастомным эмодзи Telegram может отклонить целиком, а терять заявку на
    вывод из-за оформления нельзя."""
    from app.content import emoji

    seen = {}
    original = bot.send_message

    async def spy(*args, **kwargs):
        seen['plain'] = emoji.is_plain()
        return await original(*args, **kwargs)

    bot.send_message = spy
    await container.settings.set('notify.chat_id', -100500)

    assert await notifier.registered(5)
    assert seen['plain'] is True


async def test_server_notifications_go_to_their_own_topic(bot, notifier, container):
    """Раньше они уходили в тему «payments», которой в настройках нет,
    и падали в общий чат без ветки."""
    await container.settings.set('notify.chat_id', -100500)

    await notifier.send('servers', 'тест')

    assert bot.sent[-1]['thread'] == 1561465


# ── лично админам ───────────────────────────────────────────────────────────
#
# Адреса берутся из .env, а не из настроек: то, что должно дойти лично, не
# должно зависеть от номера чата в базе — его могут поменять, чат удалить,
# права на тему потерять.

@pytest.fixture
def personal(bot, container):
    return Notifier(bot, container.settings, container.users,
                    admin_ids=(111, 222))


async def test_a_permanent_block_reaches_each_admin(personal, bot, container):
    await container.settings.set('notify.chat_id', -100500)

    await personal.torrent(5, count=3, node='NL-1', step='block', dm=True)

    personally = [row for row in bot.sent if row['chat_id'] in (111, 222)]
    assert len(personally) == 2
    assert 'отключена' in personally[0]['text']
    # и в общий чат тоже: лента остаётся полной
    assert [row for row in bot.sent if row['chat_id'] == -100500]


async def test_a_warning_does_not_go_personally(personal, bot, container):
    await container.settings.set('notify.chat_id', -100500)

    await personal.torrent(5, count=1, step='warn', dm=True)

    assert not [row for row in bot.sent if row['chat_id'] in (111, 222)]


async def test_the_personal_block_notice_says_how_to_undo_it(personal, bot,
                                                             container):
    await personal.torrent(5, count=3, step='block', dm=True)

    assert '/torrentok 5' in bot.sent[0]['text']


async def test_an_appeal_comes_personally_with_the_buttons(personal, bot,
                                                           container):
    await container.settings.set('notify.chat_id', -100500)

    await personal.torrent_appeal(5, stats={'count': 2, 'reports': 3},
                                  hint='подсказка', dm=True)

    personally = [row for row in bot.sent if row['chat_id'] in (111, 222)]
    assert len(personally) == 2
    # решать удобнее там, где прочитал
    assert personally[0]['markup'] is not None


async def test_one_unreachable_admin_does_not_stop_the_rest(bot, container):
    """Один заблокировал бота — остальные всё равно должны узнать."""
    class Picky(RecordingBot):
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            if chat_id == 111:
                raise RuntimeError('bot was blocked')
            return await RecordingBot.send_message(self, chat_id, text,
                                                   reply_markup, **kwargs)

    picky = Picky()
    notifier = Notifier(picky, container.settings, container.users,
                        admin_ids=(111, 222))

    assert await notifier.dm('важное') == 1
    assert picky.sent[0]['chat_id'] == 222


async def test_without_admin_ids_nothing_is_sent_personally(notifier, bot):
    assert await notifier.dm('важное') == 0
