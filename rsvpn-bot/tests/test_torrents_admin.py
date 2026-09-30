"""Торренты глазами админа: экран и решение по жалобе.

Сборку сервиса (attach_bot) проверяет отдельно test_bot_flow — там
container.missing_services() обязан быть пустым. Здесь харнесс админки,
и сервис в него ставится руками.
"""

import pytest

from app.bot.callbacks import Torrent
from app.core.time import now
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
