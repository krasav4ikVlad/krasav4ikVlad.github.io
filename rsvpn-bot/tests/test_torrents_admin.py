"""Торренты глазами админа: экран и решение по жалобе.

Сборку сервиса (attach_bot) проверяет отдельно test_bot_flow — там
container.missing_services() обязан быть пустым. Здесь харнесс админки,
и сервис в него ставится руками.
"""

import pytest

from app.bot.callbacks import Torrent
from app.core.time import now
from app.campaigns.sender import Sender
from app.campaigns.sender import Sender
from app.services.torrents import TorrentGuard
from tests.test_admin_panel import ADMIN, admin_env, callback, message  # noqa: F401


async def test_the_torrents_screen_opens(admin_env):
    dp, bot, session, c = admin_env
    await dp.feed_update(bot, message('/torrents'))
    assert 'Торренты' in session.last_text


async def test_the_admin_decision_reaches_the_person(admin_env):
    dp, bot, session, c = admin_env
    c.torrents = TorrentGuard(c.users, c.settings, Sender(), bot,
                              c.moderation, notifier=c.notifier)
    await c.users.create({'user_data': {'user_id': 42}, 'info': {'balance': 0},
                          'moderation': {'vpn_locked': True,
                                         'torrent': {'count': 3,
                                                     'appealed_at': now(),
                                                     'strikes': [now()]}}})

    await dp.feed_update(bot, callback(
        Torrent(action='trust', user_id=42).pack()))

    card = await c.users.get(42)
    assert not (card.get('moderation') or {}).get('vpn_locked')
    assert not (card['moderation']['torrent'] or {}).get('strikes')
    assert (card['moderation']['torrent'] or {}).get('forgiven') == 1


async def test_the_ban_command_disables_the_subscription(admin_env):
    dp, bot, session, c = admin_env
    c.torrents = TorrentGuard(c.users, c.settings, Sender(), bot,
                              c.moderation, notifier=c.notifier)
    await c.users.create({'user_data': {'user_id': 1127037964},
                          'info': {'balance': 0},
                          'vpn': {'uuid': 'u-1'}})

    await dp.feed_update(bot, message('/torrentban 1127037964 120 ГБ'))

    card = await c.users.get(1127037964)
    assert (card.get('moderation') or {}).get('vpn_locked') is True
    assert '120 ГБ' in session.last_text


async def test_the_ban_command_explains_itself_without_arguments(admin_env):
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/torrentban'))

    assert 'torrentban' in session.last_text


async def test_an_unknown_person_is_not_banned(admin_env):
    dp, bot, session, c = admin_env
    c.torrents = TorrentGuard(c.users, c.settings, Sender(), bot, c.moderation)

    await dp.feed_update(bot, message('/torrentban 5555555'))

    assert 'не найден' in session.last_text


async def test_an_admin_is_not_banned_by_mistake(admin_env):
    dp, bot, session, c = admin_env
    c.torrents = TorrentGuard(c.users, c.settings, Sender(), bot, c.moderation)
    await c.users.create({'user_data': {'user_id': ADMIN.id}, 'info': {}})

    await dp.feed_update(bot, message(f'/torrentban {ADMIN.id}'))

    card = await c.users.get(ADMIN.id)
    assert not (card.get('moderation') or {}).get('vpn_locked')


async def test_the_screen_says_when_the_panel_sent_nothing(admin_env):
    """Отчёты есть в панели, а в боте пусто — так выглядит ненастроенный
    вебхук, и экран должен сказать это словами."""
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/torrents'))

    assert 'ни одного' in session.last_text
    assert 'WEBHOOK_ENABLED' in session.last_text
