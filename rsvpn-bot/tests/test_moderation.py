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

    assert (await moderation.ban(uid, admin_id=99, reason='спам')).ok
    assert ModerationService.is_banned(await moderation.users.get(uid))

    assert (await moderation.unban(uid, admin_id=99)).ok
    assert not ModerationService.is_banned(await moderation.users.get(uid))


async def test_banning_a_stranger_reports_failure(moderation):
    assert (await moderation.ban(404404, admin_id=99)).ok is False


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


# ── жёсткий бан ─────────────────────────────────────────────────────────────
class FakePanel:
    def __init__(self, failing: set[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.failing = failing or set()

    async def set_status(self, uuid, status):
        if uuid in self.failing:
            raise RuntimeError('панель не ответила')
        self.calls.append((uuid, status))
        return {}


@pytest.fixture
def panel():
    return FakePanel()


@pytest.fixture
def hard_moderation(container, panel):
    return ModerationService(container.users, container.settings, vpn=panel)


async def test_plain_ban_leaves_the_subscription_running(hard_moderation, panel,
                                                         user_factory):
    """Обычный бан закрывает только бота — конфиг работает до конца срока."""
    user = await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bypass'})

    await hard_moderation.ban(user['user_data']['user_id'], admin_id=1)

    assert panel.calls == []


async def test_hard_ban_disables_both_subscriptions(hard_moderation, panel, user_factory):
    user = await user_factory(**{'vpn.uuid': 'main', 'vpn.bypass_uuid': 'bypass'})

    result = await hard_moderation.ban(user['user_data']['user_id'], admin_id=1, hard=True)

    assert panel.calls == [('main', 'DISABLED'), ('bypass', 'DISABLED')]
    assert result.disabled == 2
    assert result.panel_failed == 0


async def test_unban_after_a_hard_ban_turns_subscriptions_back_on(hard_moderation, panel,
                                                                  user_factory):
    user = await user_factory(**{'vpn.uuid': 'main'})
    uid = user['user_data']['user_id']

    await hard_moderation.ban(uid, admin_id=1, hard=True)
    panel.calls.clear()
    result = await hard_moderation.unban(uid, admin_id=1)

    assert panel.calls == [('main', 'ACTIVE')]
    assert result.hard is True


async def test_unban_after_a_plain_ban_does_not_touch_the_panel(hard_moderation, panel,
                                                                user_factory):
    """Ничего не выключали — включать тоже нечего."""
    user = await user_factory(**{'vpn.uuid': 'main'})
    uid = user['user_data']['user_id']

    await hard_moderation.ban(uid, admin_id=1)
    await hard_moderation.unban(uid, admin_id=1)

    assert panel.calls == []


async def test_hard_ban_holds_even_if_the_panel_fails(container, user_factory):
    """Отказ панели не должен отменять блокировку: в боте человек уже закрыт,
    а незакрытую подписку надо повторить, а не считать бан несостоявшимся."""
    service = ModerationService(container.users, container.settings,
                                vpn=FakePanel(failing={'main'}))
    user = await user_factory(**{'vpn.uuid': 'main'})
    uid = user['user_data']['user_id']

    result = await service.ban(uid, admin_id=1, hard=True)

    assert result.ok and result.panel_failed == 1
    assert ModerationService.is_banned(await service.users.get(uid))


async def test_hard_ban_without_a_subscription_is_harmless(hard_moderation, panel,
                                                           user_factory):
    user = await user_factory()

    result = await hard_moderation.ban(user['user_data']['user_id'], admin_id=1, hard=True)

    assert result.ok and result.disabled == 0
    assert panel.calls == []


async def test_ban_level_is_visible_in_the_document(hard_moderation, user_factory):
    plain = await user_factory()
    hard = await user_factory(**{'vpn.uuid': 'main'})

    await hard_moderation.ban(plain['user_data']['user_id'], admin_id=1)
    await hard_moderation.ban(hard['user_data']['user_id'], admin_id=1, hard=True)

    assert not ModerationService.is_hard(
        await hard_moderation.users.get(plain['user_data']['user_id']))
    assert ModerationService.is_hard(
        await hard_moderation.users.get(hard['user_data']['user_id']))


async def test_money_survives_a_hard_ban(hard_moderation, panel, user_factory):
    """Баланс — не предмет блокировки ни на одном уровне."""
    user = await user_factory(**{'info.balance': 500, 'vpn.uuid': 'main'})
    uid = user['user_data']['user_id']

    await hard_moderation.ban(uid, admin_id=1, hard=True)

    assert (await hard_moderation.users.get(uid))['info']['balance'] == 500
