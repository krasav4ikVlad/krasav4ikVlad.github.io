"""Копии базы из админки: /backup и /backups."""

import pytest

from app.services.backup import BackupService
from tests.test_admin_panel import ADMIN, admin_env, message  # noqa: F401


@pytest.fixture
async def ready(admin_env, tmp_path):
    dp, bot, session, c = admin_env
    await c.settings.set('backup.dir', str(tmp_path / 'b'))
    await c.settings.set('backup.separate_process', False)
    c.backup = BackupService(c.db, c.settings, name='RS_TEST')
    await c.users.create({'user_data': {'user_id': 7}, 'info': {'balance': 1}})
    return dp, bot, session, c


async def test_a_copy_can_be_made_by_hand(ready):
    dp, bot, session, c = ready

    await dp.feed_update(bot, message('/backup'))

    assert (await c.backup.last()) is not None
    assert 'готова' in session.last_text or 'Копия базы' in session.last_text


async def test_the_list_tells_where_they_are(ready):
    dp, bot, session, c = ready
    await c.backup.run()

    await dp.feed_update(bot, message('/backups'))

    assert 'Каталог' in session.last_text and 'dbrestore' in session.last_text


async def test_an_empty_list_says_so_loudly(ready):
    dp, bot, session, c = ready

    await dp.feed_update(bot, message('/backups'))

    assert 'Копий нет' in session.last_text


async def test_the_screen_shows_the_idle_time(ready):
    """«Копия делается» без времени последней записи ничего не говорит:
    именно так брошенный обрывок и выглядел работой."""
    import os
    import time

    dp, bot, session, c = ready
    folder = await c.backup.directory()
    part = folder / 'RS_TEST-2026-01-01-0055.jsonl.part'
    part.write_bytes(b'x')
    fresh = time.time() - 30
    os.utime(part, (fresh, fresh))

    await dp.feed_update(bot, message('/backups'))

    assert 'последняя запись' in session.last_text


async def test_a_forced_copy_ignores_the_marker(ready):
    dp, bot, session, c = ready
    folder = await c.backup.directory()
    (folder / 'RS_TEST-2026-01-01-0055.jsonl.part').write_bytes(b'x')

    await dp.feed_update(bot, message('/backup force'))

    assert (await c.backup.last()) is not None


async def test_a_refusal_explains_the_way_out(ready):
    dp, bot, session, c = ready
    folder = await c.backup.directory()
    (folder / 'RS_TEST-2026-01-01-0055.jsonl.part').write_bytes(b'x')

    await dp.feed_update(bot, message('/backup'))

    assert '/backup force' in session.last_text


# ── /backupcheck ────────────────────────────────────────────────────────────
#
# Команда отвечает на два вопроса сразу: «работает ли хранилище» и «куда
# именно уезжают копии». Второй нужен, когда те же ключи вписывают в другое
# место — например, в бэкап панели. Секрет при этом показывать нельзя:
# экран админки фотографируют и пересылают, а по паре ключей чужой человек
# скачивает всю базу.

class FakeStorage:
    """Хранилище, которое отвечает, но никуда не ходит."""

    def __init__(self, ready: bool = True, fail: str = ''):
        from app.core.config import StorageConfig

        self.ready = ready
        self.fail = fail
        self.config = StorageConfig(
            endpoint='https://acc123.r2.cloudflarestorage.com',
            bucket='rsvpn-db', key='AKIA0123456789abcdef',
            secret='s3cr3t-value-nobody-should-see',
            region='auto', prefix='rsvpn')

    def check(self) -> str:
        if self.fail:
            raise RuntimeError(self.fail)
        return 'rsvpn/probe.txt'


async def test_the_check_shows_where_copies_go(ready):
    dp, bot, session, c = ready
    c.backup.storage = FakeStorage()

    await dp.feed_update(bot, message('/backupcheck'))

    text = session.last_text
    assert 'acc123.r2.cloudflarestorage.com' in text
    assert 'rsvpn-db' in text and 'auto' in text


async def test_the_check_never_shows_the_secret(ready):
    """Ключ — наполовину, секрет — никогда: по этой паре скачивают базу."""
    dp, bot, session, c = ready
    c.backup.storage = FakeStorage()

    await dp.feed_update(bot, message('/backupcheck'))

    text = session.last_text
    assert 's3cr3t-value-nobody-should-see' not in text
    assert 'AKIA0123456789abcdef' not in text
    assert 'AKIA' in text and 'cdef' in text      # по краям — для узнавания


async def test_the_check_names_the_reason_when_it_fails(ready):
    dp, bot, session, c = ready
    c.backup.storage = FakeStorage(fail='AccessDenied')

    await dp.feed_update(bot, message('/backupcheck'))

    assert 'AccessDenied' in session.last_text
    assert 'Object Read' in session.last_text


async def test_without_storage_the_env_lines_are_shown(ready):
    dp, bot, session, c = ready
    c.backup.storage = FakeStorage(ready=False)

    await dp.feed_update(bot, message('/backupcheck'))

    assert 'BACKUP_S3_ENDPOINT' in session.last_text
