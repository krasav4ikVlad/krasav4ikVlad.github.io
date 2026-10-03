"""Миграции данных: перенос коллекций с пробелом в имени."""

import pytest

from app.core.db import LEGACY_RENAMES
from migrations import m0003_rename_legacy_collections as m0003


async def test_legacy_collections_are_copied(container):
    await container.db['promo_codes '].insert_one({'_id': 'WELCOME100', 'reward_value': 100})
    await container.db['churn_surveys '].insert_one({'_id': 1, 'reason': 'too_expensive'})

    await m0003.up(container)

    moved = await container.db['promo_codes'].find_one({'_id': 'WELCOME100'})
    assert moved['reward_value'] == 100
    assert await container.db['churn_surveys'].count_documents({}) == 1


async def test_migration_is_idempotent(container):
    await container.db['promo_codes '].insert_one({'_id': 'X', 'used_count': 5})

    await m0003.up(container)
    await m0003.up(container)

    assert await container.db['promo_codes'].count_documents({}) == 1


async def test_old_data_is_kept_for_rollback(container):
    await container.db['promo_usages '].insert_one({'_id': 1})
    await m0003.up(container)
    assert await container.db['promo_usages '].count_documents({}) == 1
    assert set(LEGACY_RENAMES.values()) == {
        'support_quick_replies', 'churn_surveys', 'promo_codes', 'promo_usages'}


# ── безопасность на живой базе ──────────────────────────────────────────────
async def test_legacy_mode_skips_collection_copy(container, monkeypatch):
    """С LEGACY_COLLECTIONS=1 бот и так читает старые имена — копии не нужны."""
    import dataclasses

    container.config = dataclasses.replace(container.config, legacy_collections=True)
    await container.db['promo_codes '].insert_one({'_id': 'X', 'code': 'X'})

    await m0003.up(container)

    assert await container.db['promo_codes'].count_documents({}) == 0


async def test_transactions_migration_needs_explicit_confirmation(container, monkeypatch):
    """Она переписывает массив целиком — на работающих платежах это опасно."""
    from migrations import m0002_transactions_format as m0002

    monkeypatch.delenv('MIGRATE_FORCE', raising=False)
    await container.db['users'].insert_one({
        '_id': 1, 'user_data': {'user_id': 1},
        'info': {'transactions': [[100, 'дата', 'Пополнение']]}})

    await m0002.up(container)

    doc = await container.db['users'].find_one({'_id': 1})
    assert doc['info']['transactions'][0] == [100, 'дата', 'Пополнение']   # не тронуто

    monkeypatch.setenv('MIGRATE_FORCE', '1')
    await m0002.up(container)

    doc = await container.db['users'].find_one({'_id': 1})
    assert doc['info']['transactions'][0]['amount'] == 100


async def test_failed_index_keeps_migration_unapplied(container):
    """Иначе повторный запуск скажет «уже применена», а индекса нет."""
    from migrations import m0001_indexes as m0001

    await container.db['users'].insert_one({'_id': 1, 'user_data': {'user_id': 5}})
    await container.db['users'].insert_one({'_id': 2, 'user_data': {'user_id': 5}})

    with pytest.raises(RuntimeError, match='не созданы индексы'):
        await m0001.up(container)


async def test_startup_does_not_raise_for_the_bot(container):
    """Бот должен подняться даже с проблемным индексом — иначе он просто не работает."""
    await container.db['users'].insert_one({'_id': 1, 'user_data': {'user_id': 5}})
    await container.db['users'].insert_one({'_id': 2, 'user_data': {'user_id': 5}})

    await container.startup()      # без strict — не бросает
    assert container.users.failed_indexes


async def test_redo_clears_the_applied_mark(container):
    from migrations.runner import apply_all

    await container.db['migrations'].insert_one({'_id': 'm0001_indexes'})

    await apply_all(container, only=['m9999'], redo=['m0001'])

    assert await container.db['migrations'].count_documents({'_id': 'm0001_indexes'}) == 0
