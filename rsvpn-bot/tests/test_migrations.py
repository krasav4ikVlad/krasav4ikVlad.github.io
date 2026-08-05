"""Миграции данных: перенос коллекций с пробелом в имени."""

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
