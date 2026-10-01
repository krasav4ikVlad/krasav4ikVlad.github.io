"""Пульс процесса: память и то, как бот умер в прошлый раз.

Разбор, который без этого шёл трое суток: pm2 знает только, что процесс
кончился, а логи показывают, что было ДО смерти, — но не говорят, была ли
это смерть от лимита памяти или падение кода.
"""

import json

import pytest

from app.services import pulse


@pytest.fixture(autouse=True)
def here(tmp_path, monkeypatch):
    monkeypatch.setattr(pulse, 'FILE', tmp_path / 'heartbeat.json')
    return tmp_path / 'heartbeat.json'


def test_the_memory_is_measured():
    """Под Linux — из /proc, а бот живёт именно там."""
    assert pulse.rss_mb() > 0


def test_a_beat_writes_down_the_memory_and_the_build(here):
    pulse.beat()

    mark = json.loads(here.read_text(encoding='utf-8'))
    assert mark['rss'] > 0 and mark['build'] and mark['stopped'] is False


def test_a_killed_process_leaves_its_last_memory(here):
    """Главное: по этой отметке видно, на какой памяти процесс убили."""
    pulse.beat()

    death = pulse.last_death()

    assert death and death['rss'] > 0


def test_an_honest_goodbye_is_not_a_death(here):
    pulse.beat()
    pulse.stopped()

    assert pulse.last_death() == {}


def test_the_first_run_has_nothing_to_report(here):
    assert pulse.last_death() == {}


def test_a_broken_file_is_not_a_death_either(here):
    """Испорченная отметка не должна превращаться в ложную тревогу."""
    here.parent.mkdir(parents=True, exist_ok=True)
    here.write_text('не json', encoding='utf-8')

    assert pulse.last_death() == {}


async def test_the_job_warns_when_the_memory_is_high(here, monkeypatch):
    from app.scheduler import jobs

    told = []

    class Notifier:
        async def dm(self, text, markup=None):
            told.append(text)
            return 1

    class Container:
        notifier = Notifier()

    monkeypatch.setattr(pulse, 'rss_mb', lambda: pulse.WARN_MB + 50)

    await jobs.watch_memory(Container(), bot=None)

    assert told and 'max_memory_restart' in told[0]


async def test_a_calm_bot_is_not_bothered(here, monkeypatch):
    from app.scheduler import jobs

    told = []

    class Notifier:
        async def dm(self, text, markup=None):
            told.append(text)
            return 1

    class Container:
        notifier = Notifier()

    monkeypatch.setattr(pulse, 'rss_mb', lambda: 50.0)

    await jobs.watch_memory(Container(), bot=None)

    assert not told


async def test_the_warning_is_not_repeated_every_minute(here, monkeypatch):
    from app.scheduler import jobs

    told = []

    class Notifier:
        async def dm(self, text, markup=None):
            told.append(text)
            return 1

    class Container:
        notifier = Notifier()

    monkeypatch.setattr(pulse, 'rss_mb', lambda: pulse.WARN_MB + 50)
    container = Container()

    for _ in range(5):
        await jobs.watch_memory(container, bot=None)

    assert len(told) == 1
