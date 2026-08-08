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


async def test_payout_card_carries_requisites_and_buttons(notifier, bot):
    await notifier.payout_requested(5, amount=900, method='m1',
                                    details='<b>Тип:</b> СБП\n• Банк: Сбербанк',
                                    username='ivan')

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

    sent = await notifier.payout_requested(5, amount=result.amount, method=result.method)
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
