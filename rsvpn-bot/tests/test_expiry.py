"""Напоминания об истечении: разбор события, однократность, фильтры."""

import hashlib
import hmac

import pytest

from app.core.security import verify_hmac_signature as verify_signature
from app.campaigns.sender import Sender
from app.repositories.users import UsersRepository
from app.services.expiry import ExpiryNotifier, key_for_hours
from app.settings.service import SettingsService

from tests.conftest import FakeBot


@pytest.fixture
def notifier(db):
    bot = FakeBot()
    service = ExpiryNotifier(
        UsersRepository(db['users']), SettingsService(db['bot_settings']), Sender(), bot)
    return service, bot


# ── разбор meta.expiration ──────────────────────────────────────────────────
def test_negative_hours_mean_time_left():
    assert key_for_hours(-72) == '3d'
    assert key_for_hours(-48) == '2d'
    assert key_for_hours(-24) == '1d'
    assert key_for_hours(-6) == '6h'
    assert key_for_hours(-1) == '1h'


def test_positive_hours_mean_already_expired():
    assert key_for_hours(0) == 'expired'
    assert key_for_hours(24) == 'expired_24h'
    assert key_for_hours(100) == 'expired_72h'


def test_custom_panel_thresholds_fall_back_to_nearest():
    """В панели можно выставить свои пороги — бот не должен молчать."""
    assert key_for_hours(-36) == '1d'     # 36 ч осталось → ближайший порог «сутки»
    assert key_for_hours(-2) == '1h'


# ── подпись ─────────────────────────────────────────────────────────────────
def test_signature_matches_and_rejects():
    body, secret = b'{"event":"user.expired"}', 'panel-secret'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(body, good, secret) is True
    assert verify_signature(body, f'sha256={good}', secret) is True
    assert verify_signature(body, 'nope', secret) is False
    assert verify_signature(body, None, secret) is False
    assert verify_signature(body, None, '') is True    # секрет не настроен


# ── отправка ────────────────────────────────────────────────────────────────
async def test_sends_reminder_and_marks_it(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1', 'info.balance': 50})

    result = await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})

    assert result['note'] == 'sent_1d' and len(bot.sent) == 1
    assert 'завтра' in bot.sent[0][1]
    user = await db['users'].find_one({'vpn.uuid': 'u-1'})
    assert user['vpn']['notified']['1d'] is True


async def test_retry_of_the_same_webhook_sends_once(db, user_factory, notifier):
    """Панель ретраит доставку — второе сообщение уйти не должно."""
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})

    first = await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})
    second = await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})

    assert first['note'] == 'sent_1d'
    assert second['note'] == 'already_sent_1d'
    assert len(bot.sent) == 1


async def test_renewal_resets_flags(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})

    await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})
    await service.reset_after_renewal(1)
    await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})

    assert len(bot.sent) == 2


async def test_bypass_subscription_is_ignored(db, user_factory, notifier):
    """У человека две подписки в панели — напоминать про ByPass не нужно."""
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bp'})

    result = await service.handle('user.expiration', {'uuid': 'bp'}, {'expiration': -24})

    assert result['note'] == 'bypass_ignored' and bot.sent == []


async def test_each_threshold_can_be_switched_off(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})
    await service.settings.set('expiry.send_3h', False)

    result = await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -3})

    assert result['note'] == 'skipped_3h' and bot.sent == []


async def test_all_notifications_can_be_switched_off(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})
    await service.settings.set('expiry.notify_enabled', False)

    result = await service.handle('user.expiration', {'uuid': 'u-1'}, {'expiration': -24})

    assert result['note'] == 'disabled' and bot.sent == []


async def test_expired_event_sends_final_reminder(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})

    result = await service.handle('user.expired', {'uuid': 'u-1'}, {})

    assert result['note'] == 'sent_expired'
    assert 'закончилась' in bot.sent[0][1]


async def test_legacy_event_names_still_work(db, user_factory, notifier):
    """Панель до 2.8.0 присылает старые имена событий."""
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})

    result = await service.handle('user.expires_in_72_hours', {'uuid': 'u-1'}, {})
    assert result['note'] == 'sent_3d'


async def test_user_found_by_telegram_id_and_short_uuid(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.shortUuid': 'short-1'})
    await user_factory(**{'vpn.uuid': 'x'})

    by_short = await service.handle('user.expired', {'shortUuid': 'short-1'}, {})
    by_tg = await service.handle('user.expired', {'telegramId': '2'}, {})

    assert by_short['user_id'] == 1 and by_tg['user_id'] == 2


async def test_unknown_user_is_not_an_error(db, notifier):
    """Ответ всё равно 200: иначе панель уйдёт в бесконечные ретраи."""
    service, bot = notifier
    result = await service.handle('user.expired', {'uuid': 'ghost'}, {})
    assert result['ok'] is True and result['note'] == 'user_not_found'


async def test_unrelated_events_are_skipped(db, user_factory, notifier):
    service, bot = notifier
    await user_factory(**{'vpn.uuid': 'u-1'})
    result = await service.handle('user.first_connected', {'uuid': 'u-1'}, {})
    assert result['note'] == 'ignored_user.first_connected' and bot.sent == []
