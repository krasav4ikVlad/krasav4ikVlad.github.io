"""Копия базы наружу: S3-совместимое хранилище.

Копия на том же сервере не спасает ровно от того, ради чего всё
затевалось: аккаунт на хостинге забанили — сервер пропал вместе со всеми
снимками.
"""

import pytest

from app.core.config import StorageConfig
from app.integrations.storage import S3Storage


class FakeS3:
    def __init__(self, broken: bool = False, items=()):
        self.uploaded: list[tuple] = []
        self.deleted: list[str] = []
        self.broken = broken
        self.items = list(items)

    def upload_file(self, path, bucket, key):
        if self.broken:
            raise RuntimeError('ключи не подошли')
        self.uploaded.append((path, bucket, key))

    def list_objects_v2(self, Bucket, Prefix=''):  # noqa: N803 — имя из boto3
        return {'Contents': self.items}

    def delete_object(self, Bucket, Key):          # noqa: N803
        self.deleted.append(Key)


@pytest.fixture
def config():
    return StorageConfig(endpoint='https://s3.example', bucket='rs',
                         key='k', secret='s', prefix='rsvpn')


def test_without_keys_nothing_happens(config):
    """Хранилище не настроено — это не ошибка, это «не просили»."""
    assert S3Storage(StorageConfig()).ready is False
    assert S3Storage(config, FakeS3()).ready is True


def test_the_file_goes_under_the_prefix(config):
    fake = FakeS3()

    key = S3Storage(config, fake).upload('/tmp/RS_2-2026-10-01.jsonl.gz')

    assert key == 'rsvpn/RS_2-2026-10-01.jsonl.gz'
    assert fake.uploaded[0][1] == 'rs'


def test_a_refusal_is_not_swallowed(config):
    """Молча не уехавшая копия — худший вид отсутствующей копии."""
    with pytest.raises(RuntimeError):
        S3Storage(config, FakeS3(broken=True)).upload('/tmp/x.gz')


def test_old_copies_are_removed_there_too(config):
    """Платить за год ежедневных снимков никто не подписывался."""
    fake = FakeS3(items=[{'Key': f'rsvpn/{n}', 'LastModified': n}
                         for n in range(20)])

    removed = S3Storage(config, fake).rotate(keep=5)

    assert removed == 15
    # удаляем самые старые, а не что попало
    assert 'rsvpn/0' in fake.deleted and 'rsvpn/19' not in fake.deleted


async def test_the_copy_is_sent_away_after_it_is_verified(db, tmp_path):
    """Сначала снимок и проверка, потом отправка: наружу должно уехать
    только то, что уже признано копией."""
    from app.services.backup import BackupService
    from app.settings.service import SettingsService

    settings = SettingsService(db['bot_settings'])
    await settings.set('backup.dir', str(tmp_path))
    await settings.set('backup.separate_process', False)
    await db['users'].insert_one({'user_data': {'user_id': 1}})

    fake = FakeS3()
    service = BackupService(db, settings, 'RS_TEST',
                            storage=S3Storage(
                                StorageConfig(endpoint='https://s3.example',
                                              bucket='rs', key='k', secret='s'),
                                fake))

    report = await service.run()

    assert report.ok and report.stored
    assert fake.uploaded[0][0] == report.path


async def test_a_storage_failure_does_not_cancel_the_copy(db, tmp_path):
    """Файл уже на диске и уже проверен — это копия. Не уехала наружу —
    скажем словами, но несделанной её называть нельзя."""
    from app.services.backup import BackupService
    from app.settings.service import SettingsService

    settings = SettingsService(db['bot_settings'])
    await settings.set('backup.dir', str(tmp_path))
    await settings.set('backup.separate_process', False)
    await db['users'].insert_one({'user_data': {'user_id': 1}})

    service = BackupService(db, settings, 'RS_TEST',
                            storage=S3Storage(
                                StorageConfig(endpoint='https://s3.example',
                                              bucket='rs', key='k', secret='s'),
                                FakeS3(broken=True)))

    report = await service.run()

    assert report.ok and not report.stored
    assert 'ключи не подошли' in report.store_error


async def test_it_can_be_switched_off(db, tmp_path):
    from app.services.backup import BackupService
    from app.settings.service import SettingsService

    settings = SettingsService(db['bot_settings'])
    await settings.set('backup.dir', str(tmp_path))
    await settings.set('backup.separate_process', False)
    await settings.set('backup.to_storage', False)
    await db['users'].insert_one({'user_data': {'user_id': 1}})

    fake = FakeS3()
    service = BackupService(db, settings, 'RS_TEST',
                            storage=S3Storage(
                                StorageConfig(endpoint='https://s3.example',
                                              bucket='rs', key='k', secret='s'),
                                fake))

    assert (await service.run()).ok
    assert not fake.uploaded
