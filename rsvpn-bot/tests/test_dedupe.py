"""Удаление дублей: оставляем первую запись, спорные группы не трогаем."""

import pytest

from app.services.dedupe import DedupeService


@pytest.fixture
def dedupe(db):
    return DedupeService(db['users'], db['users_dupes_backup'])


async def add(db, _id, user_id, **fields):
    doc = {'_id': _id, 'user_data': {'user_id': user_id}, 'info': {'balance': 0}}
    for path, value in fields.items():
        current = doc
        parts = path.split('.')
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    await db['users'].insert_one(doc)
    return doc


async def test_no_duplicates_no_groups(db, dedupe):
    await add(db, 'a', 1)
    await add(db, 'b', 2)

    report = await dedupe.scan('user_data.user_id')
    assert report.scanned == 2 and report.groups == []


async def test_keeps_the_earliest_record(db, dedupe):
    """find_one возвращает первую запись — её и оставляем."""
    await add(db, 'a1', 100)
    await add(db, 'a2', 100)
    await add(db, 'a3', 100)

    report = await dedupe.scan('user_data.user_id')

    assert len(report.groups) == 1
    group = report.groups[0]
    assert group.keep['_id'] == 'a1'
    assert [d['_id'] for d in group.remove] == ['a2', 'a3']


async def test_scan_changes_nothing(db, dedupe):
    await add(db, 'a1', 100)
    await add(db, 'a2', 100)

    await dedupe.scan('user_data.user_id')
    assert await db['users'].count_documents({}) == 2


async def test_deletes_extra_and_keeps_backup(db, dedupe):
    await add(db, 'a1', 100)
    await add(db, 'a2', 100)

    report = await dedupe.scan('user_data.user_id')
    deleted = await dedupe.delete(report)

    assert deleted == 1
    assert await db['users'].count_documents({}) == 1
    assert (await db['users'].find_one({}))['_id'] == 'a1'

    backup = await db['users_dupes_backup'].find_one({'original_id': 'a2'})
    assert backup['kept_id'] == 'a1' and backup['document']['_id'] == 'a2'


async def test_group_with_money_in_duplicate_is_not_touched(db, dedupe):
    """Второй документ с балансом — удалять нельзя, деньги пропадут."""
    await add(db, 'a1', 100)
    await add(db, 'a2', 100, **{'info.balance': 500})

    report = await dedupe.scan('user_data.user_id')
    deleted = await dedupe.delete(report)

    assert deleted == 0
    assert len(report.risky) == 1
    assert await db['users'].count_documents({}) == 2


async def test_group_with_subscription_in_duplicate_is_not_touched(db, dedupe):
    await add(db, 'a1', 100)
    await add(db, 'a2', 100, **{'vpn.shortUuid': 'live-sub'})

    report = await dedupe.scan('user_data.user_id')
    assert report.risky and 'vpn.shortUuid' in report.risky[0].conflicts
    assert await dedupe.delete(report) == 0


async def test_risky_group_deleted_only_with_force(db, dedupe):
    await add(db, 'a1', 100)
    await add(db, 'a2', 100, **{'info.balance': 500})

    report = await dedupe.scan('user_data.user_id')
    deleted = await dedupe.delete(report, include_risky=True)

    assert deleted == 1
    assert await db['users'].count_documents({}) == 1
    # копия сохранена — данные восстановимы
    backup = await db['users_dupes_backup'].find_one({'original_id': 'a2'})
    assert backup['document']['info']['balance'] == 500


async def test_duplicate_without_data_is_safe(db, dedupe):
    await add(db, 'a1', 100, **{'info.balance': 300, 'vpn.shortUuid': 'sub'})
    await add(db, 'a2', 100)      # пустышка

    report = await dedupe.scan('user_data.user_id')
    assert report.safe and not report.risky
    assert await dedupe.delete(report) == 1


async def test_works_for_any_collection_and_field(db):
    service = DedupeService(db['promo_codes'], db['promo_backup'])
    await db['promo_codes'].insert_one({'_id': 1, 'code': 'SALE'})
    await db['promo_codes'].insert_one({'_id': 2, 'code': 'SALE'})
    await db['promo_codes'].insert_one({'_id': 3, 'code': 'OTHER'})

    report = await service.scan('code')
    assert len(report.groups) == 1
    assert await service.delete(report) == 1
    assert await db['promo_codes'].count_documents({}) == 2


async def test_documents_without_the_field_are_skipped(db, dedupe):
    await db['users'].insert_one({'_id': 'x', 'info': {}})
    await db['users'].insert_one({'_id': 'y', 'info': {}})

    report = await dedupe.scan('user_data.user_id')
    assert report.groups == []


class AggregatingCollection:
    """Коллекция, умеющая aggregate — как настоящая Mongo."""

    def __init__(self, inner):
        self.inner = inner
        self.aggregate_calls = 0

    def aggregate(self, pipeline, **kwargs):
        self.aggregate_calls += 1
        field = pipeline[0]['$group']['_id'].lstrip('$')

        async def run():
            counts: dict = {}
            async for doc in self.inner.find({}):
                from app.services.dedupe import pick
                key = pick(doc, field)
                if key is not None:
                    counts[key] = counts.get(key, 0) + 1
            for key, count in counts.items():
                if count > 1:
                    yield {'_id': key}

        return run()

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def test_uses_server_side_grouping_when_available(db):
    """На 200k документов выкачивать всё нельзя — группировка идёт в Mongo."""
    from app.services.dedupe import DedupeService

    await add(db, 'a1', 100)
    await add(db, 'a2', 100)
    await add(db, 'b1', 200)

    collection = AggregatingCollection(db['users'])
    service = DedupeService(collection, db['users_dupes_backup'])

    report = await service.scan('user_data.user_id')

    assert collection.aggregate_calls == 1
    assert len(report.groups) == 1
    assert report.groups[0].keep['_id'] == 'a1'
