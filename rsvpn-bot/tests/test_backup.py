"""Копии базы: снимок, проверка, уборка старых и обратная дорога.

Главное здесь — последнее. Снимок, из которого нельзя восстановиться, это
не копия, а ложное спокойствие: выясняется это в единственный день, когда
копия нужна. Поэтому проверяется не «файл создался», а «данные вернулись
такими же, включая даты и вложенные документы».
"""

import gzip
import json
from datetime import datetime, timedelta

import pytest

from app.core.time import now
from app.services.backup import BackupService, human_size
from app.settings.service import SettingsService


@pytest.fixture
def settings(db):
    return SettingsService(db['bot_settings'])


@pytest.fixture
def folder(tmp_path):
    return tmp_path / 'backups'


@pytest.fixture
async def service(db, settings, folder):
    await settings.set('backup.dir', str(folder))
    return BackupService(db, settings, name='RS_TEST')


async def fill(db, users: int = 3) -> None:
    for number in range(1, users + 1):
        await db['users'].insert_one({
            'user_data': {'user_id': number, 'username': f'u{number}',
                          'date_joined': now() - timedelta(days=number)},
            'info': {'balance': number * 100,
                     'ref_stats': {'referrals': [number + 100]}}})
    await db['bot_settings'].insert_one({'_id': 'price.month', 'value': 199})


def lines(path) -> list[dict]:
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ── снимок ──────────────────────────────────────────────────────────────────
async def test_a_snapshot_holds_every_collection(service, db, folder):
    await fill(db)

    report = await service.run()

    assert report.ok
    assert report.collections['users'] == 3
    assert 'bot_settings' in report.collections      # настройки тоже данные
    assert list(folder.glob('*.jsonl.gz'))


async def test_the_snapshot_is_read_back_before_it_counts(service, db):
    """Архив, который не открывается, — не архив. Считаем строки обратно."""
    await fill(db)

    report = await service.run()

    assert report.checked == report.docs


async def test_a_half_written_snapshot_does_not_look_ready(service, db, folder):
    """Прерванный снимок не должен остаться в каталоге — ни как готовый,
    ни как мусор. Поэтому ломаемся ПОСЛЕ того, как файл уже начали писать."""
    await fill(db)
    original = service._dump

    async def broken(target, *args, **kwargs):
        await original(target)
        raise RuntimeError('диск кончился')

    service._dump = broken
    report = await service.run()

    assert not report.ok and 'диск' in report.error
    assert not list(folder.glob('*.jsonl.gz'))
    assert not list(folder.glob('*.part'))


async def test_leftovers_from_a_killed_process_are_swept(service, db, folder):
    """Процесс могли убить посреди записи — тогда unlink не отработал."""
    await fill(db)
    folder.mkdir(parents=True, exist_ok=True)
    junk = folder / 'RS_TEST-2020-01-01-0000.jsonl.part'
    junk.write_bytes(b'x')

    await service.run()

    assert not junk.exists()


async def test_a_mismatch_is_a_failure(service, db, folder):
    """Записали одно, прочиталось другое — это не «почти получилось»."""
    await fill(db)
    service._verify = staticmethod(lambda path: 1)

    report = await service.run()

    assert not report.ok and 'читается' in report.error
    assert not list(folder.glob('*.jsonl.gz'))


async def test_the_name_says_what_and_when(service, db):
    await fill(db)

    report = await service.run()

    assert report.path.split('/')[-1].startswith('RS_TEST-')
    assert now().strftime('%Y-%m-%d') in report.path


# ── уборка ──────────────────────────────────────────────────────────────────
async def test_old_snapshots_are_removed(service, db, settings, folder):
    await fill(db)
    await settings.set('backup.keep', 2)
    for number in range(4):
        old = folder / f'RS_TEST-2020-01-0{number + 1}-0000.jsonl.gz'
        folder.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b'x')

    report = await service.run()

    assert report.removed == 3
    assert len(list(folder.glob('*.jsonl.gz'))) == 2


async def test_the_newest_are_the_ones_kept(service, db, settings, folder):
    await fill(db)
    await settings.set('backup.keep', 1)
    await service.run()
    fresh = (await service.files())[0]

    await service.rotate()

    assert fresh.exists()


# ── пора ли ─────────────────────────────────────────────────────────────────
async def test_without_a_single_copy_it_is_always_time(service, db):
    assert await service.due() is True


async def test_right_after_a_copy_it_is_not_time(service, db, settings):
    await fill(db)
    await service.run()

    assert await service.due() is False


async def test_a_day_later_it_is_time_again(service, db, settings, folder):
    import os

    await fill(db)
    await service.run()
    stale = (await service.files())[0]
    long_ago = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(stale, (long_ago, long_ago))

    assert await service.due() is True
    assert (await service.age_hours()) > 24


async def test_it_can_be_switched_off_by_the_period(service, db, settings):
    await settings.set('backup.every_hours', 0)

    assert await service.due() is False


# ── обратная дорога ─────────────────────────────────────────────────────────
#
# Ради этого всё и делается. Проверяем не «файл создался», а что данные
# вернулись такими же: даты датами, вложенные документы на месте.

async def test_the_data_comes_back(service, db, folder):
    from scripts.dbrestore import read_lines

    await fill(db)
    report = await service.run()

    restored = {}
    for collection, doc in read_lines(folder / report.path.split('/')[-1]):
        restored.setdefault(collection, []).append(doc)

    users = sorted(restored['users'], key=lambda doc: doc['user_data']['user_id'])
    assert len(users) == 3
    assert users[0]['info']['balance'] == 100
    assert users[0]['info']['ref_stats']['referrals'] == [101]


async def test_the_dates_stay_dates(service, db, folder):
    """Если сохранить дату строкой, после восстановления сломается всё, что
    считает сроки, — и заметить это можно будет очень нескоро."""
    from scripts.dbrestore import read_lines

    await fill(db)
    report = await service.run()

    doc = next(doc for name, doc in read_lines(folder / report.path.split('/')[-1])
               if name == 'users')
    assert isinstance(doc['user_data']['date_joined'], datetime)


async def test_a_broken_line_is_named_not_swallowed(folder):
    from scripts.dbrestore import read_lines

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'broken.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        handle.write('{"c": "users", "d": {"_id": 1}}\n')
        handle.write('это не json\n')

    with pytest.raises(RuntimeError, match='строка 2'):
        list(read_lines(path))


async def test_a_foreign_file_is_refused(folder):
    """Чужой gz-файл не должен молча «восстановиться» ничем."""
    from scripts.dbrestore import read_lines

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'alien.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        handle.write('{"что-то": "чужое"}\n')

    with pytest.raises(RuntimeError, match='не похожа на снимок'):
        list(read_lines(path))


async def test_the_archive_can_be_inspected_without_touching_anything(service, db,
                                                                      folder):
    """Команда восстановления сначала показывает, что внутри."""
    from scripts.dbrestore import inspect

    await fill(db)
    report = await service.run()

    counts = inspect(folder / report.path.split('/')[-1])

    assert counts['users'] == 3 and counts['bot_settings'] >= 1


# ── мелочи, которые видно человеку ──────────────────────────────────────────
def test_the_size_is_readable():
    assert human_size(512) == '512 Б'
    assert human_size(1536) == '1.5 КБ'
    assert human_size(5 * 1024 * 1024) == '5.0 МБ'


# ── по расписанию, само ─────────────────────────────────────────────────────
#
# Смысл всей затеи в том, что никто ничего не запускает руками. Значит
# проверять надо задачу планировщика, а не только сервис.

class Container:
    """Ровно то, что нужно задаче."""

    def __init__(self, backup, settings, notifier=None):
        self.backup = backup
        self.settings = settings
        self.notifier = notifier

        class Health:
            def __init__(self):
                self.marks = []

            async def mark(self, key, **info):
                self.marks.append((key, info))

        self.health = Health()


class Told:
    def __init__(self):
        self.done: list[dict] = []
        self.failed: list[str] = []
        self.files: list[str] = []

    async def backup_done(self, name, size, docs, seconds=0, removed=0):
        self.done.append({'name': name, 'size': size, 'docs': docs})
        return True

    async def backup_failed(self, error):
        self.failed.append(error)
        return True

    async def backup_file(self, path, limit_mb=45):
        self.files.append(path)
        return True


async def test_the_job_makes_a_copy_when_it_is_time(service, db, settings, folder):
    from app.scheduler import jobs

    await fill(db)
    told = Told()

    await jobs.backup_database(Container(service, settings, told), bot=None)

    assert list(folder.glob('*.jsonl.gz')) and told.done


async def test_the_job_does_nothing_right_after_a_copy(service, db, settings):
    from app.scheduler import jobs

    await fill(db)
    await service.run()
    told = Told()

    await jobs.backup_database(Container(service, settings, told), bot=None)

    assert not told.done


async def test_the_job_obeys_the_switch(service, db, settings, folder):
    from app.scheduler import jobs

    await fill(db)
    await settings.set('backup.enabled', False)

    await jobs.backup_database(Container(service, settings, Told()), bot=None)

    assert not list(folder.glob('*.jsonl.gz'))


async def test_a_failure_is_shouted_about(service, db, settings):
    """Молчащий бекап неотличим от работающего ровно до того дня, когда он
    понадобится."""
    from app.scheduler import jobs

    async def broken(*args, **kwargs):
        raise RuntimeError('диск кончился')

    await fill(db)
    service._dump = broken
    told = Told()

    await jobs.backup_database(Container(service, settings, told), bot=None)

    assert told.failed and not told.done


async def test_the_file_is_not_sent_to_the_chat_by_default(service, db,
                                                           settings):
    """В снимке вся база: почты, платежи, переписка. В чат — только если
    это включили нарочно."""
    from app.scheduler import jobs

    await fill(db)
    told = Told()

    await jobs.backup_database(Container(service, settings, told), bot=None)

    assert told.done and not told.files


async def test_the_file_is_sent_when_asked(service, db, settings):
    from app.scheduler import jobs

    await fill(db)
    await settings.set('backup.to_telegram', True)
    told = Told()

    await jobs.backup_database(Container(service, settings, told), bot=None)

    assert told.files


# ── полный круг: снять и залить обратно ─────────────────────────────────────
#
# Единственная проверка, ради которой всё это писалось. Остальные меряют
# части; эта — что из копии действительно можно восстановиться.

class Replace:
    """Замена ReplaceOne из pymongo — с теми же полями, которые читает
    драйвер. Нужна потому, что сам pymongo в тестовом окружении не
    поднимается (ему нужны бинарные библиотеки), а проверять здесь надо
    не драйвер, а то, что пачки и очистка собираются правильно."""

    def __init__(self, doc):
        self._filter = {'_id': doc['_id']}
        self._doc = doc


@pytest.fixture(autouse=True)
def _no_driver(monkeypatch):
    from scripts import dbrestore

    monkeypatch.setattr(dbrestore, 'replace_op', Replace)


async def snapshot(service, db, folder):
    report = await service.run()
    assert report.ok
    return folder / report.path.split('/')[-1]


async def test_a_wiped_database_comes_back(service, db, folder):
    from scripts.dbrestore import write_docs

    await fill(db)
    archive = await snapshot(service, db, folder)
    before = sorted(doc['user_data']['user_id'] for doc in db['users'].docs)

    db['users'].docs.clear()                      # «базу снесли»
    written = await write_docs(db, archive, drop=False)

    assert written['users'] == 3
    assert sorted(doc['user_data']['user_id']
                  for doc in db['users'].docs) == before


async def test_restoring_twice_does_not_double_anything(service, db, folder):
    """Команду наберут дважды — «а вдруг не прошло»."""
    from scripts.dbrestore import write_docs

    await fill(db)
    archive = await snapshot(service, db, folder)
    db['users'].docs.clear()

    await write_docs(db, archive, drop=False)
    await write_docs(db, archive, drop=False)

    assert len(db['users'].docs) == 3


async def test_a_deleted_user_returns_without_touching_the_rest(service, db,
                                                                folder):
    """Так чинят случайное удаление: вернуть пропавшее, не потеряв нового."""
    from scripts.dbrestore import write_docs

    await fill(db)
    archive = await snapshot(service, db, folder)
    await db['users'].delete_one({'user_data.user_id': 2})
    await db['users'].insert_one({'user_data': {'user_id': 99}, 'info': {}})

    await write_docs(db, archive, drop=False)

    ids = sorted(doc['user_data']['user_id'] for doc in db['users'].docs)
    assert ids == [1, 2, 3, 99]


async def test_drop_returns_the_base_exactly_to_the_snapshot(service, db, folder):
    """А так — «откатить всё как было», вместе с удалением лишнего."""
    from scripts.dbrestore import write_docs

    await fill(db)
    archive = await snapshot(service, db, folder)
    await db['users'].insert_one({'user_data': {'user_id': 99}, 'info': {}})

    await write_docs(db, archive, drop=True)

    ids = sorted(doc['user_data']['user_id'] for doc in db['users'].docs)
    assert ids == [1, 2, 3]


async def test_a_changed_document_is_replaced_not_merged(service, db, folder):
    """Заливка должна вернуть документ целиком: поле, которого в снимке
    нет, не должно пережить восстановление."""
    from scripts.dbrestore import write_docs

    await fill(db)
    archive = await snapshot(service, db, folder)
    await db['users'].update_one({'user_data.user_id': 1},
                                 {'$set': {'info.balance': 999999,
                                           'info.мусор': 'откуда-то'}})

    await write_docs(db, archive, drop=False)

    doc = next(d for d in db['users'].docs if d['user_data']['user_id'] == 1)
    assert doc['info']['balance'] == 100 and 'мусор' not in doc['info']
