"""Пересчёт сегментов пользователей.

Перенос из utils.update_users_segments. Кампании выбирают людей по
`growth.segment`, поэтому без этой задачи рассылки просто никого не найдут.

Правила расчёта живут в domain/segments.py — их можно проверить тестом,
не поднимая базу.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.time import now
from app.domain.segments import determine

log = logging.getLogger(__name__)


@dataclass
class SegmentReport:
    total: int = 0
    updated: int = 0
    failed: int = 0
    by_segment: dict[str, int] = field(default_factory=dict)


class SegmentService:
    def __init__(self, users, settings=None):
        self.users = users
        self.settings = settings

    async def run(self) -> SegmentReport:
        report = SegmentReport()
        moment = now()

        async for user in self.users.iterate({}, {
            'user_data': 1, 'info.balance': 1, 'info.transactions': 1,
            'vpn.shortUuid': 1, 'vpn.expireAt': 1, 'growth': 1,
        }):
            report.total += 1
            try:
                growth = determine(user, moment)
            except Exception:
                report.failed += 1
                log.exception('сегмент не посчитан для %s',
                              self.users.pick(user, 'user_data.user_id'))
                continue

            segment = growth['segment']
            report.by_segment[segment] = report.by_segment.get(segment, 0) + 1

            # Точечный $set, а не замена всего growth целиком. В growth.* живут
            # поля, которые считает не эта задача: growth.blocked_bot (кто
            # заблокировал бота) и growth.trial_reset_at (сброс триала). Замена
            # поддокумента стирала их каждый час — рассылка снова била в
            # заблокировавших, а сброшенный триал молча откатывался.
            update: dict = {'$set': {f'growth.{k}': v for k, v in growth.items()}}
            previous = self.users.pick(user, 'growth.segment')
            if previous and previous != segment:
                update['$push'] = {'growth_history': {
                    'from': previous, 'to': segment, 'changed_at': moment}}

            await self.users.col.update_one({'_id': user['_id']}, update)
            report.updated += 1

        log.info('сегменты: всего=%s обновлено=%s ошибок=%s',
                 report.total, report.updated, report.failed)
        return report
