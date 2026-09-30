"""Снимок базы: сам, каждый день, с проверкой и уборкой старых.

Зачем внутри бота, а не `mongodump` по крону. Бот уже знает адрес базы,
уже к ней подключён и уже умеет писать админам, когда что-то сломалось.
Крон на сервере — это ещё одно место, которое настраивают один раз и
забывают; молчащий крон неотличим от работающего, и выясняется это в
единственный неподходящий день.

Формат — построчный JSON (bson.json_util, то есть с сохранением типов:
даты остаются датами, ObjectId — ObjectId), сжатый gzip. Каждая строка:

    {"c": "users", "d": {...документ...}}

Почему не `mongodump`: его может не быть на машине, он требует отдельной
установки инструментов Mongo и своей версии под версию сервера. Здесь
восстановление — это `python -m scripts.dbrestore файл`, и работает оно
везде, где работает сам бот.

Снимок сразу после записи прочитывается обратно: архив, который не
открывается, — это не архив, а ложное спокойствие. Несовпадение числа
документов считается провалом.
"""

from __future__ import annotations

import gzip
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.time import now

log = logging.getLogger(__name__)

SYSTEM_PREFIX = 'system.'
SUFFIX = '.jsonl.gz'
BATCH = 500

# Куда класть, если не указано иначе. Нарочно вне каталога бота: `git pull`
# в обновлении не должен даже теоретически соседствовать со снимками.
DEFAULT_DIR = '~/rsvpn-backups'
DEFAULT_KEEP = 14
DEFAULT_EVERY_HOURS = 24


@dataclass
class BackupReport:
    ok: bool = False
    path: str = ''
    size: int = 0
    docs: int = 0
    checked: int = 0
    seconds: float = 0.0
    collections: dict = field(default_factory=dict)
    removed: int = 0
    error: str = ''


def human_size(size: int) -> str:
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if size < 1024 or unit == 'ГБ':
            return f'{size:.0f} {unit}' if unit == 'Б' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} ГБ'


class BackupService:
    def __init__(self, db, settings, name: str = ''):
        self.db = db
        self.settings = settings
        self.name = name or getattr(db, 'name', 'db')

    # ── куда и сколько хранить ──────────────────────────────────────────────
    async def directory(self) -> Path:
        raw = str(await self.settings.get('backup.dir') or '').strip()
        path = Path(os.path.expanduser(raw or DEFAULT_DIR))
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def keep(self) -> int:
        value = await self.settings.int('backup.keep')
        return int(DEFAULT_KEEP if value is None else value)

    async def every_hours(self) -> int:
        value = await self.settings.int('backup.every_hours')
        return int(DEFAULT_EVERY_HOURS if value is None else value)

    async def files(self) -> list[Path]:
        """Снимки от свежего к старому."""
        folder = await self.directory()
        found = [item for item in folder.glob(f'*{SUFFIX}') if item.is_file()]
        return sorted(found, key=lambda item: item.stat().st_mtime, reverse=True)

    async def last(self) -> Path | None:
        found = await self.files()
        return found[0] if found else None

    # ── снимок ──────────────────────────────────────────────────────────────
    async def run(self) -> BackupReport:
        started = time.monotonic()
        report = BackupReport()
        try:
            folder = await self.directory()
        except Exception as exc:      # noqa: BLE001 — нет каталога, нет снимка
            report.error = f'каталог недоступен: {exc}'
            log.error('бекап: %s', report.error)
            return report

        stamp = now().strftime('%Y-%m-%d-%H%M')
        target = folder / f'{self.name}-{stamp}{SUFFIX}'
        # Пишем во временный файл и переименовываем в конце: прерванный на
        # середине снимок не должен выглядеть как готовый.
        temp = target.with_suffix('.part')

        try:
            report.docs, report.collections = await self._dump(temp)
            report.checked = self._verify(temp)
            if report.checked != report.docs:
                raise RuntimeError(f'записано {report.docs}, '
                                   f'читается {report.checked}')
            temp.rename(target)
        except Exception as exc:      # noqa: BLE001 — причина уходит наверх
            temp.unlink(missing_ok=True)
            report.error = str(exc)[:300]
            report.seconds = time.monotonic() - started
            log.error('бекап не сделан: %s', report.error)
            return report

        report.ok = True
        report.path = str(target)
        report.size = target.stat().st_size
        report.seconds = time.monotonic() - started
        report.removed = await self.rotate()
        log.warning('бекап готов: %s, %s, документов %s, за %.1f с',
                    target.name, human_size(report.size), report.docs,
                    report.seconds)
        return report

    async def _dump(self, target: Path) -> tuple[int, dict]:
        from bson.json_util import dumps

        names = [name for name in await self.db.list_collection_names()
                 if not name.startswith(SYSTEM_PREFIX)]

        total = 0
        counts: dict[str, int] = {}
        with gzip.open(target, 'wt', encoding='utf-8') as handle:
            for name in sorted(names):
                written = 0
                async for doc in self.db[name].find({}):
                    handle.write(dumps({'c': name, 'd': doc}) + '\n')
                    written += 1
                counts[name] = written
                total += written
        return total, counts

    @staticmethod
    def _verify(target: Path) -> int:
        """Прочитать снимок обратно. Архив, который не открывается, — это
        не архив, а ложное спокойствие."""
        read = 0
        with gzip.open(target, 'rt', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    read += 1
        return read

    async def rotate(self) -> int:
        """Убрать лишние снимки. Возвращает, сколько удалили."""
        keep = await self.keep()
        if keep <= 0:
            return 0

        removed = 0
        # Недописанные куски от убитого процесса: unlink в run() до них не
        # дошёл, а сами они не исчезнут и будут занимать место молча.
        for junk in (await self.directory()).glob('*.part'):
            try:
                junk.unlink()
            except OSError:
                pass
        for item in (await self.files())[keep:]:
            try:
                item.unlink()
                removed += 1
            except OSError as exc:
                log.warning('бекап %s не удалён: %s', item.name, exc)
        return removed

    # ── пора ли ─────────────────────────────────────────────────────────────
    async def due(self) -> bool:
        """Прошло ли достаточно времени с последнего снимка.

        По файлам, а не по отметке в базе: снимок — это файл на диске, и
        именно его наличие отвечает на вопрос «есть ли у нас копия».
        Заодно переживает перезапуск бота и потерю базы.
        """
        hours = await self.every_hours()
        if hours <= 0:
            return False
        last = await self.last()
        if last is None:
            return True
        age = time.time() - last.stat().st_mtime
        return age >= hours * 3600

    async def age_hours(self) -> float | None:
        last = await self.last()
        if last is None:
            return None
        return (time.time() - last.stat().st_mtime) / 3600
