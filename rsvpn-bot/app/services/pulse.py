"""Пульс процесса: сколько памяти ест бот и как он в прошлый раз умер.

Появилось из разбора, который шёл трое суток. `pm2 describe` показывал
четыреста с лишним перезапусков, но ответа «почему» в нём нет: pm2 знает
только, что процесс кончился. Логи показывают, что было ДО смерти, но не
говорят, была ли она смертью от лимита памяти или падением.

Поэтому раз в минуту сюда пишется отметка: время, память, версия сборки.
При старте бот её читает. Если отметка свежая, а пометки о честной
остановке нет — прошлый процесс не завершился, а был убит, и видно, на
какой памяти это случилось. Одна строка вместо трёх суток догадок.

Файлом, а не в базе: память меряют как раз тогда, когда всё плохо, и
зависеть от базы здесь нельзя.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.core.time import now

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / 'logs' / 'heartbeat.json'

# Выше этого — предупреждаем: у pm2 в ecosystem.config.js стоит
# max_memory_restart, и подходить к нему вплотную значит однажды умереть
# посреди работы.
WARN_MB = 400
REMIND_MIN = 60


def rss_mb() -> float:
    """Сколько памяти занимает процесс сейчас. 0 — узнать не вышло."""
    try:
        with open('/proc/self/status', encoding='utf-8') as handle:
            for line in handle:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception as exc:      # noqa: BLE001 — не Linux или нет /proc
        log.debug('память не прочитана: %s', exc)
    return 0.0


def read() -> dict:
    try:
        return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:      # noqa: BLE001 — нет файла или он испорчен
        return {}


def write(**fields) -> None:
    from app.version import VERSION

    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        mark = read()
        mark.update({'at': now().isoformat(), 'rss': rss_mb(),
                     'pid': os.getpid(), 'build': VERSION, **fields})
        FILE.write_text(json.dumps(mark, ensure_ascii=False), encoding='utf-8')
    except OSError as exc:
        log.debug('пульс не записан: %s', exc)


def beat() -> float:
    """Отметка «я жив». Возвращает текущую память."""
    memory = rss_mb()
    write(stopped=False)
    return memory


def stopped() -> None:
    """Отметка «меня остановили по-человечески»."""
    write(stopped=True)


def told_recently(minutes: int = REMIND_MIN) -> bool:
    """Говорили ли про память недавно.

    Память запоминаем в файле, а не в процессе: когда бота убивают каждые
    четверть часа, отметка в памяти процесса не живёт — и человек получает
    одно и то же сообщение снова и снова, по разу на перезапуск.
    """
    from datetime import timedelta

    from app.core.time import parse_dt

    at = parse_dt(read().get('memory_told_at'))
    return bool(at and now() - at < timedelta(minutes=minutes))


def told_now() -> None:
    mark = read()
    mark['memory_told_at'] = now().isoformat()
    try:
        FILE.write_text(json.dumps(mark, ensure_ascii=False), encoding='utf-8')
    except OSError as exc:
        log.debug('отметка о памяти не записана: %s', exc)


def last_death() -> dict:
    """Что известно о прошлой смерти. Пусто — её не было.

    «Не было» — это и первый запуск, и честная остановка: в обоих случаях
    докладывать не о чем.
    """
    mark = read()
    if not mark or mark.get('stopped'):
        return {}
    return mark
