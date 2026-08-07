"""Блокировки: сервис и middleware."""

import pytest

from app.bot.middlewares.ban import BanMiddleware
from app.services.moderation import ModerationService


@pytest.fixture
def moderation(container):
    return ModerationService(container.users, container.settings)


async def test_ban_and_unban_round_trip(moderation, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    assert await moderation.ban(uid, admin_id=99, reason='спам') is True
    assert ModerationService.is_banned(await moderation.users.get(uid))

    assert await moderation.unban(uid, admin_id=99) is True
    assert not ModerationService.is_banned(await moderation.users.get(uid))


async def test_banning_a_stranger_reports_failure(moderation):
    assert await moderation.ban(404404, admin_id=99) is False


async def test_reason_and_author_are_recorded(moderation, user_factory):
    """Через полгода должно быть понятно, кто и за что заблокировал."""
    user = await user_factory()
    await moderation.ban(user['user_data']['user_id'], admin_id=77, reason='мошенничество')

    saved = (await moderation.users.get(user['user_data']['user_id']))['moderation']
    assert saved['reason'] == 'мошенничество'
    assert saved['banned_by'] == 77
    assert saved['banned_at']


async def test_banned_list_and_count(moderation, user_factory):
    for _ in range(3):
        user = await user_factory()
        await moderation.ban(user['user_data']['user_id'], admin_id=1)
    await user_factory()                       # не забанен

    assert await moderation.count() == 3
    assert len(await moderation.banned()) == 3


async def test_user_is_found_by_id_and_by_username(moderation, user_factory):
    user = await user_factory(**{'user_data.username': 'petya'})
    uid = user['user_data']['user_id']

    assert (await moderation.find_user(str(uid)))['user_data']['user_id'] == uid
    assert (await moderation.find_user('@petya'))['user_data']['user_id'] == uid
    assert (await moderation.find_user('petya'))['user_data']['user_id'] == uid
    assert await moderation.find_user('нет такого') is None


# ── middleware ──────────────────────────────────────────────────────────────
class Event:
    def __init__(self):
        self.answers: list[str] = []

    async def answer(self, text='', **kwargs):
        self.answers.append(text)


async def run(middleware, user: dict | None, tg_user=None) -> bool:
    """Возвращает, дошёл ли апдейт до хендлера."""
    reached = []

    async def handler(event, data):
        reached.append(True)

    await middleware(handler, Event(), {'user': user, 'event_from_user': tg_user})
    return bool(reached)


class TgUser:
    def __init__(self, user_id):
        self.id = user_id


async def test_ordinary_user_passes(container):
    middleware = BanMiddleware(container.settings, admin_ids=())
    assert await run(middleware, {'moderation': {'banned': False}}, TgUser(5)) is True


async def test_banned_user_is_stopped(container):
    middleware = BanMiddleware(container.settings, admin_ids=())
    assert await run(middleware, {'moderation': {'banned': True}}, TgUser(5)) is False


async def test_admin_is_never_blocked(container):
    """Иначе одной опечаткой в id можно отрезать себя от собственной админки."""
    middleware = BanMiddleware(container.settings, admin_ids=(5,))
    assert await run(middleware, {'moderation': {'banned': True}}, TgUser(5)) is True


async def test_unknown_user_passes(container):
    """Первый /start: документа ещё нет — блокировать нечего."""
    middleware = BanMiddleware(container.settings, admin_ids=())
    assert await run(middleware, None, TgUser(5)) is True
