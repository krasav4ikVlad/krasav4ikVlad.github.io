"""Создание индексов на живой базе: конфликты имён и остатки дублей."""

import pytest

from app.repositories.base import Repository, index_name
from app.repositories.users import UsersRepository


def test_index_name_matches_mongo_rules():
    assert index_name('user_data.user_id') == 'user_data.user_id_1'
    assert index_name([('promo_id', 1), ('user_id', 1)]) == 'promo_id_1_user_id_1'


async def test_existing_non_unique_index_is_recreated(db):
    """Индекс, созданный вручную для ускорения поиска дублей, мешал миграции."""
    repo = Repository(db['users'])
    await repo.ensure_index('user_data.user_id')          # как будто создан руками

    assert await repo.ensure_index('user_data.user_id', unique=True) is True
    assert db['users'].indexes['user_data.user_id_1'] is True


async def test_duplicates_block_unique_index_with_clear_message(db, caplog):
    await db['users'].insert_one({'_id': 1, 'user_data': {'user_id': 100}})
    await db['users'].insert_one({'_id': 2, 'user_data': {'user_id': 100}})

    repo = Repository(db['users'])
    created = await repo.ensure_index('user_data.user_id', unique=True)

    assert created is False
    assert 'dedupe' in caplog.text


async def test_startup_survives_index_problems(db):
    """Бот не должен падать при старте из-за индекса."""
    await db['users'].insert_one({'_id': 1, 'user_data': {'user_id': 100}})
    await db['users'].insert_one({'_id': 2, 'user_data': {'user_id': 100}})

    await UsersRepository(db['users']).ensure_indexes()      # не бросает

    assert db['users'].indexes.get('growth.segment_1') is False   # остальные созданы


async def test_index_is_created_when_data_is_clean(db):
    await db['users'].insert_one({'_id': 1, 'user_data': {'user_id': 100}})
    await db['users'].insert_one({'_id': 2, 'user_data': {'user_id': 200}})

    assert await Repository(db['users']).ensure_index('user_data.user_id',
                                                      unique=True) is True
