"""Поиск и удаление дублей в коллекции.

Дубли появились потому, что регистрация в старом боте делает `find_one`,
а потом `insert_one` без уникального индекса: два быстрых /start подряд
создают две записи. Дальше весь код работает с ПЕРВОЙ (find_one возвращает
её), а вторая висит мёртвым грузом — но иногда именно в неё попадают деньги
или подписка, если она оказалась первой в другой момент.

Поэтому скрипт:
  * оставляет самую раннюю запись (по _id — он монотонный по времени создания);
  * группы, где в удаляемых записях есть данные, которых нет в оставляемой
    (баланс, подписка, транзакции), помечает как спорные и НЕ трогает без
    отдельного разрешения;
  * перед удалением складывает копии в отдельную коллекцию.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.time import now

log = logging.getLogger(__name__)

# Поля, потеря которых заметна: если они есть в дубле, но не в оставляемой
# записи — группу нужно смотреть руками.
VALUABLE_FIELDS = ('info.balance', 'vpn.shortUuid', 'vpn.uuid',
                   'info.transactions', 'info.ref_stats.withdrawable')


def pick(doc: dict, path: str) -> Any:
    current: Any = doc
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def has_value(doc: dict, path: str) -> bool:
    value = pick(doc, path)
    if value in (None, '', 0, [], {}):
        return False
    return True


@dataclass
class DuplicateGroup:
    key: Any
    keep: dict
    remove: list[dict]
    conflicts: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not self.conflicts


@dataclass
class DedupeReport:
    scanned: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    deleted: int = 0

    @property
    def safe(self) -> list[DuplicateGroup]:
        return [g for g in self.groups if g.is_safe]

    @property
    def risky(self) -> list[DuplicateGroup]:
        return [g for g in self.groups if not g.is_safe]

    @property
    def extra_documents(self) -> int:
        return sum(len(g.remove) for g in self.groups)


class DedupeService:
    def __init__(self, collection, backup_collection=None):
        self.col = collection
        self.backup = backup_collection

    async def scan(self, field_path: str) -> DedupeReport:
        """Группирует документы по полю. Ничего не меняет."""
        report = DedupeReport()
        buckets: dict[Any, list[dict]] = {}

        async for doc in self.col.find({}):
            report.scanned += 1
            key = pick(doc, field_path)
            if key is None:
                continue
            buckets.setdefault(key, []).append(doc)

        for key, docs in buckets.items():
            if len(docs) < 2:
                continue

            # самая ранняя запись — та, которую и возвращает find_one
            docs.sort(key=lambda d: str(d.get('_id')))
            keep, remove = docs[0], docs[1:]

            conflicts = []
            for path in VALUABLE_FIELDS:
                if not has_value(keep, path) and any(has_value(d, path) for d in remove):
                    conflicts.append(path)
                elif path == 'info.balance':
                    lost = sum(int(pick(d, path) or 0) for d in remove)
                    if lost > 0:
                        conflicts.append(f'{path} (в дублях {lost}₽)')

            report.groups.append(DuplicateGroup(key, keep, remove, conflicts))

        return report

    async def delete(self, report: DedupeReport, include_risky: bool = False) -> int:
        """Удаляет дубли, предварительно сохранив их копии."""
        groups = report.groups if include_risky else report.safe
        deleted = 0

        for group in groups:
            for doc in group.remove:
                if self.backup is not None:
                    await self.backup.insert_one({
                        'original_id': doc.get('_id'), 'key': group.key,
                        'kept_id': group.keep.get('_id'), 'removed_at': now(),
                        'document': doc,
                    })
                await self.col.delete_one({'_id': doc['_id']})
                deleted += 1

        report.deleted = deleted
        log.info('удалено дублей: %s (спорных групп пропущено: %s)',
                 deleted, 0 if include_risky else len(report.risky))
        return deleted
