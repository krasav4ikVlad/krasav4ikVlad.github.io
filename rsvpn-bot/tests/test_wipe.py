"""Удаление тестового пользователя.

Смысл команды — пройти сценарий новичка заново. Поэтому главная проверка
здесь не «документ исчез», а «после удаления регистрация проходит как у
нового человека»: подписка в панели снята, хвосты в других коллекциях
убраны, промокод снова можно активировать.
"""

import pytest

from app.services.wipe import WipeService


class Panel:
    def __init__(self, fail: bool = False):
        self.deleted: list[str] = []
        self.fail = fail

    async def delete_subscription(self, uuid):
        if self.fail:
            raise RuntimeError('панель недоступна')
        self.deleted.append(uuid)
        return True


@pytest.fixture
def wipe(container):
    container.wipe.vpn = Panel()
    return container.wipe


async def twink(container, user_id: int = 5, **vpn) -> dict:
    doc = {'user_data': {'user_id': user_id, 'username': 'twink'},
           'info': {'balance': 100},
           'vpn': {'shortUuid': 's', 'uuid': 'u-main', **vpn}}
    await container.users.create(doc)
    return doc


# ── база ────────────────────────────────────────────────────────────────────
async def test_user_document_is_gone(container, wipe):
    await twink(container)

    result = await wipe.wipe(5)

    assert result.ok
    assert await container.users.get(5) is None


async def test_deleting_a_stranger_is_not_an_error(container, wipe):
    result = await wipe.wipe(999)

    assert result.ok is False
    assert wipe.vpn.deleted == []


# ── панель ──────────────────────────────────────────────────────────────────
async def test_panel_subscriptions_are_removed(container, wipe):
    """Без этого повторная регистрация упрётся в «short UUID already exists»:
    shortUuid считается от user_id и совпадёт со старым."""
    await twink(container, bypass_uuid='u-bypass')

    result = await wipe.wipe(5)

    assert set(wipe.vpn.deleted) == {'u-main', 'u-bypass'}
    assert result.panel_deleted == 2


async def test_panel_failure_still_clears_the_database(container):
    """Панель может лежать. База чистится всё равно, а отказ виден в отчёте —
    иначе о брошенной подписке никто не узнает."""
    container.wipe.vpn = Panel(fail=True)
    await twink(container)

    result = await container.wipe.wipe(5)

    assert result.panel_failed == 1
    assert await container.users.get(5) is None


async def test_user_without_a_subscription_needs_no_panel(container, wipe):
    await container.users.create({'user_data': {'user_id': 7}, 'vpn': {}})

    result = await wipe.wipe(7)

    assert result.ok and wipe.vpn.deleted == []


# ── хвосты ──────────────────────────────────────────────────────────────────
async def test_traces_in_other_collections_are_removed(container, wipe, db):
    """Твинк вернётся «новым», только если за ним не тянутся старые записи."""
    await twink(container)
    await db['payments'].insert_one({'user_id': 5, 'txid': 't1'})
    await db['promo_usages'].insert_one({'user_id': 5, 'code': 'HELLO'})
    await db['gifts'].insert_one({'from_user_id': 5, 'gift_id': 'g1'})
    await db['fingerprint_assignments'].insert_one({'user_id': 5, 'squads': ['a']})

    result = await wipe.wipe(5)

    assert await db['payments'].count_documents({'user_id': 5}) == 0
    assert await db['promo_usages'].count_documents({'user_id': 5}) == 0
    assert await db['gifts'].count_documents({'from_user_id': 5}) == 0
    assert await db['fingerprint_assignments'].count_documents({'user_id': 5}) == 0
    assert result.total >= 5


async def test_gifts_are_removed_from_both_sides(container, wipe, db):
    await twink(container)
    await db['gifts'].insert_one({'from_user_id': 9, 'to_user_id': 5})

    await wipe.wipe(5)

    assert await db['gifts'].count_documents({}) == 0


async def test_other_users_are_not_touched(container, wipe, db):
    await twink(container, user_id=5)
    await twink(container, user_id=6)
    await db['payments'].insert_one({'user_id': 6, 'txid': 't6'})

    await wipe.wipe(5)

    assert await container.users.get(6) is not None
    assert await db['payments'].count_documents({'user_id': 6}) == 1


async def test_preview_changes_nothing(container, wipe, db):
    await twink(container)
    await db['payments'].insert_one({'user_id': 5, 'txid': 't1'})

    found = await wipe.preview(5)

    assert found['payments'] == 1
    assert await container.users.get(5) is not None


# ── повторное прохождение сценария ──────────────────────────────────────────
async def test_the_promo_code_can_be_used_again(container, wipe):
    """Ровно то, ради чего команда и нужна: пройти акцию второй раз.

    Повторную активацию не даёт уникальный индекс в promo_usages, а не флаг
    в документе человека, — значит без чистки этой коллекции удаление
    твинка ничего бы не изменило.
    """
    promo = container.promo
    await promo.ensure_indexes()
    await promo.codes.insert_one({
        'code': 'HELLO', 'reward_type': 'balance', 'reward_value': 50,
        'max_uses': 0, 'used_count': 0, 'is_active': True, 'expires_at': None})
    await twink(container)

    assert (await promo.redeem(5, 'HELLO')).ok
    assert not (await promo.redeem(5, 'HELLO')).ok          # второй раз — отказ

    await wipe.wipe(5)
    await twink(container)

    assert (await promo.redeem(5, 'HELLO')).ok, 'после удаления промокод снова доступен'


async def test_the_free_period_is_available_again(container, wipe):
    """Метка триала лежит в документе — вместе с ним и уходит."""
    await container.users.create({
        'user_data': {'user_id': 5}, 'vpn': {},
        'growth': {'trial_claimed_at': 'когда-то'}})

    assert container.trial.claimed(await container.users.get(5))

    await wipe.wipe(5)
    await container.users.create({'user_data': {'user_id': 5}, 'vpn': {}})

    assert not container.trial.claimed(await container.users.get(5))


# ── защита от промаха ───────────────────────────────────────────────────────
async def test_service_does_not_know_about_admins(container, wipe):
    """Проверка на администратора живёт в админке, а не здесь: сервис —
    исполнитель. Тест фиксирует это разделение, чтобы никто не искал
    защиту не в том файле."""
    assert not hasattr(WipeService, 'admin_ids')
